"""
API Nodes for Gemini Multimodal LLM Usage via Remote API
See: https://cloud.google.com/vertex-ai/generative-ai/docs/model-reference/inference
"""
from __future__ import annotations

import json
import time
import os
import uuid
import base64
from io import BytesIO
from enum import Enum
from typing import Optional, Literal

import torch

import folder_paths
from comfy.comfy_types.node_typing import IO, ComfyNodeABC, InputTypeDict
from server import PromptServer
from comfy_api_nodes.apis import (
    GeminiContent,
    GeminiGenerateContentRequest,
    GeminiGenerateContentResponse,
    GeminiInlineData,
    GeminiPart,
    GeminiMimeType,
)
from comfy_api_nodes.apis.gemini_api import GeminiImageGenerationConfig, GeminiImageGenerateContentRequest, GeminiImageConfig
from comfy_api_nodes.apis.client import (
    ApiEndpoint,
    HttpMethod,
    SynchronousOperation,
)
from comfy_api_nodes.apinode_utils import (
    validate_string,
    audio_to_base64_string,
    video_to_base64_string,
    tensor_to_base64_string,
    bytesio_to_image_tensor,
)
from comfy_api.util import VideoContainer, VideoCodec
from comfy_api_nodes.gemini_error_handler import (
    create_error_details,
    log_error_details,
    create_user_friendly_error_message,
)


# Use Google's direct API instead of ComfyUI proxy
GEMINI_BASE_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/"
GEMINI_MAX_INPUT_FILE_SIZE = 20 * 1024 * 1024  # 20 MB

# VERIFICATION LOG: Confirm URL fix is deployed
print("=" * 80)
print("🔍 GEMINI URL FIX CONFIRMED - MODULE LOADED")
print(f"   Base URL: {GEMINI_BASE_ENDPOINT}")
print(f"   Has trailing slash: {GEMINI_BASE_ENDPOINT.endswith('/')}")
print("=" * 80)


class GeminiModel(str, Enum):
    """
    Gemini Model Names allowed by comfy-api
    """

    gemini_2_5_pro_preview_05_06 = "gemini-2.5-pro-preview-05-06"
    gemini_2_5_flash_preview_04_17 = "gemini-2.5-flash-preview-04-17"
    gemini_2_5_pro = "gemini-2.5-pro"
    gemini_2_5_flash = "gemini-2.5-flash"


class GeminiImageModel(str, Enum):
    """
    Gemini Image Model Names allowed by comfy-api
    """

    gemini_2_5_flash_image_preview = "gemini-2.5-flash-image-preview"
    gemini_2_5_flash_image = "gemini-2.5-flash-image"


def get_gemini_endpoint(
    model: GeminiModel,
) -> ApiEndpoint[GeminiGenerateContentRequest, GeminiGenerateContentResponse]:
    """
    Get the API endpoint for a given Gemini model.

    Args:
        model: The Gemini model to use, either as enum or string value.

    Returns:
        ApiEndpoint configured for the specific Gemini model.
    """
    if isinstance(model, str):
        model = GeminiModel(model)
    return ApiEndpoint(
        path=f"{model.value}:generateContent",
        method=HttpMethod.POST,
        request_model=GeminiGenerateContentRequest,
        response_model=GeminiGenerateContentResponse,
    )


def get_gemini_image_endpoint(
    model: GeminiImageModel,
) -> ApiEndpoint[GeminiGenerateContentRequest, GeminiGenerateContentResponse]:
    """
    Get the API endpoint for a given Gemini model.

    Args:
        model: The Gemini model to use, either as enum or string value.

    Returns:
        ApiEndpoint configured for the specific Gemini model.
    """
    if isinstance(model, str):
        model = GeminiImageModel(model)
    return ApiEndpoint(
        path=f"{model.value}:generateContent",
        method=HttpMethod.POST,
        request_model=GeminiImageGenerateContentRequest,
        response_model=GeminiGenerateContentResponse,
    )


def create_image_parts(image_input: torch.Tensor) -> list[GeminiPart]:
    """
    Convert image tensor input to Gemini API compatible parts.

    Args:
        image_input: Batch of image tensors from ComfyUI.

    Returns:
        List of GeminiPart objects containing the encoded images.
    """
    image_parts: list[GeminiPart] = []
    for image_index in range(image_input.shape[0]):
        image_as_b64 = tensor_to_base64_string(
            image_input[image_index].unsqueeze(0)
        )
        image_parts.append(
            GeminiPart(
                inlineData=GeminiInlineData(
                    mimeType=GeminiMimeType.image_png,
                    data=image_as_b64,
                )
            )
        )
    return image_parts


def create_text_part(text: str) -> GeminiPart:
    """
    Create a text part for the Gemini API request.

    Args:
        text: The text content to include in the request.

    Returns:
        A GeminiPart object with the text content.
    """
    return GeminiPart(text=text)


def get_parts_from_response(
    response: GeminiGenerateContentResponse
) -> list[GeminiPart]:
    """
    Extract all parts from the Gemini API response.

    Args:
        response: The API response from Gemini.

    Returns:
        List of response parts from the first candidate.
    """
    # Debug logging to understand why candidates might be None
    if response.candidates is None:
        print(f"[EmProps] ERROR: Gemini response.candidates is None")
        print(f"[EmProps] Full response object: {response}")
        print(f"[EmProps] Response dict: {response.__dict__ if hasattr(response, '__dict__') else 'N/A'}")
        raise ValueError(
            "Gemini API returned no candidates. This typically indicates content filtering, "
            "API quota/rate limits, or invalid request. Check the full response above for details."
        )

    if len(response.candidates) == 0:
        print(f"[EmProps] ERROR: Gemini response.candidates is empty list")
        print(f"[EmProps] Full response object: {response}")
        raise ValueError(
            "Gemini API returned empty candidates list. This typically indicates content filtering "
            "or API rejection. Check the full response above for details."
        )

    return response.candidates[0].content.parts


def get_parts_by_type(
    response: GeminiGenerateContentResponse, part_type: Literal["text"] | str
) -> list[GeminiPart]:
    """
    Filter response parts by their type.

    Args:
        response: The API response from Gemini.
        part_type: Type of parts to extract ("text" or a MIME type).

    Returns:
        List of response parts matching the requested type.
    """
    parts = []
    for part in get_parts_from_response(response):
        if part_type == "text" and hasattr(part, "text") and part.text:
            parts.append(part)
        elif (
            hasattr(part, "inlineData")
            and part.inlineData
            and part.inlineData.mimeType == part_type
        ):
            parts.append(part)
        # Skip parts that don't match the requested type
    return parts


def get_text_from_response(response: GeminiGenerateContentResponse) -> str:
    """
    Extract and concatenate all text parts from the response.

    Args:
        response: The API response from Gemini.

    Returns:
        Combined text from all text parts in the response.
    """
    parts = get_parts_by_type(response, "text")
    return "\n".join([part.text for part in parts])


def get_image_from_response(response: GeminiGenerateContentResponse) -> torch.Tensor:
    image_tensors: list[torch.Tensor] = []
    parts = get_parts_by_type(response, "image/png")
    for part in parts:
        image_data = base64.b64decode(part.inlineData.data)
        returned_image = bytesio_to_image_tensor(BytesIO(image_data))
        image_tensors.append(returned_image)
    if len(image_tensors) == 0:
        return torch.zeros((1,1024,1024,4))
    return torch.cat(image_tensors, dim=0)


class GeminiNode(ComfyNodeABC):
    """
    Node to generate text responses from a Gemini model.

    This node allows users to interact with Google's Gemini AI models, providing
    multimodal inputs (text, images, audio, video, files) to generate coherent
    text responses. The node works with the latest Gemini models, handling the
    API communication and response parsing.
    """

    @classmethod
    def INPUT_TYPES(cls) -> InputTypeDict:
        return {
            "required": {
                "prompt": (
                    IO.STRING,
                    {
                        "multiline": True,
                        "default": "",
                        "tooltip": "Text inputs to the model, used to generate a response. You can include detailed instructions, questions, or context for the model.",
                    },
                ),
                "model": (
                    IO.COMBO,
                    {
                        "tooltip": "The Gemini model to use for generating responses.",
                        "options": [model.value for model in GeminiModel],
                        "default": GeminiModel.gemini_2_5_pro.value,
                    },
                ),
                "seed": (
                    IO.INT,
                    {
                        "default": 42,
                        "min": 0,
                        "max": 0xFFFFFFFFFFFFFFFF,
                        "control_after_generate": True,
                        "tooltip": "When seed is fixed to a specific value, the model makes a best effort to provide the same response for repeated requests. Deterministic output isn't guaranteed. Also, changing the model or parameter settings, such as the temperature, can cause variations in the response even when you use the same seed value. By default, a random seed value is used.",
                    },
                ),
            },
            "optional": {
                "images": (
                    IO.IMAGE,
                    {
                        "default": None,
                        "tooltip": "Optional image(s) to use as context for the model. To include multiple images, you can use the Batch Images node.",
                    },
                ),
                "audio": (
                    IO.AUDIO,
                    {
                        "tooltip": "Optional audio to use as context for the model.",
                        "default": None,
                    },
                ),
                "video": (
                    IO.VIDEO,
                    {
                        "tooltip": "Optional video to use as context for the model.",
                        "default": None,
                    },
                ),
                "files": (
                    "GEMINI_INPUT_FILES",
                    {
                        "default": None,
                        "tooltip": "Optional file(s) to use as context for the model. Accepts inputs from the Gemini Generate Content Input Files node.",
                    },
                ),
            },
            "hidden": {
                "gemini_api_key": "GEMINI_API_KEY",
                "unique_id": "UNIQUE_ID",
            },
        }

    DESCRIPTION = "Generate text responses with Google's Gemini AI model. You can provide multiple types of inputs (text, images, audio, video) as context for generating more relevant and meaningful responses."
    RETURN_TYPES = ("STRING",)
    FUNCTION = "api_call"
    CATEGORY = "api node/text/Gemini"
    API_NODE = True

    def create_video_parts(self, video_input: IO.VIDEO, **kwargs) -> list[GeminiPart]:
        """
        Convert video input to Gemini API compatible parts.

        Args:
            video_input: Video tensor from ComfyUI.
            **kwargs: Additional arguments to pass to the conversion function.

        Returns:
            List of GeminiPart objects containing the encoded video.
        """

        base_64_string = video_to_base64_string(
            video_input,
            container_format=VideoContainer.MP4,
            codec=VideoCodec.H264
        )
        return [
            GeminiPart(
                inlineData=GeminiInlineData(
                    mimeType=GeminiMimeType.video_mp4,
                    data=base_64_string,
                )
            )
        ]

    def create_audio_parts(self, audio_input: IO.AUDIO) -> list[GeminiPart]:
        """
        Convert audio input to Gemini API compatible parts.

        Args:
            audio_input: Audio input from ComfyUI, containing waveform tensor and sample rate.

        Returns:
            List of GeminiPart objects containing the encoded audio.
        """
        audio_parts: list[GeminiPart] = []
        for batch_index in range(audio_input["waveform"].shape[0]):
            # Recreate an IO.AUDIO object for the given batch dimension index
            audio_at_index = {
                "waveform": audio_input["waveform"][batch_index].unsqueeze(0),
                "sample_rate": audio_input["sample_rate"],
            }
            # Convert to MP3 format for compatibility with Gemini API
            audio_bytes = audio_to_base64_string(
                audio_at_index,
                container_format="mp3",
                codec_name="libmp3lame",
            )
            audio_parts.append(
                GeminiPart(
                    inlineData=GeminiInlineData(
                        mimeType=GeminiMimeType.audio_mp3,
                        data=audio_bytes,
                    )
                )
            )
        return audio_parts

    async def api_call(
        self,
        prompt: str,
        model: GeminiModel,
        seed: int,
        images: Optional[IO.IMAGE] = None,
        audio: Optional[IO.AUDIO] = None,
        video: Optional[IO.VIDEO] = None,
        files: Optional[list[GeminiPart]] = None,
        unique_id: Optional[str] = None,
        gemini_api_key: Optional[str] = None,
    ) -> tuple[str]:
        response = None
        request_data = None

        try:
            # Validate inputs
            validate_string(prompt, strip_whitespace=False)

            # Create parts list with text prompt as the first part
            parts: list[GeminiPart] = [create_text_part(prompt)]

            # Add other modal parts
            if images is not None:
                image_parts = create_image_parts(images)
                parts.extend(image_parts)
            if audio is not None:
                parts.extend(self.create_audio_parts(audio))
            if video is not None:
                parts.extend(self.create_video_parts(video))
            if files is not None:
                parts.extend(files)

            # WORKAROUND: ComfyUI's hidden parameter injection is broken, read directly from os.environ
            import os
            gemini_api_key = os.environ.get('GEMINI_API_KEY')

            # Validate API key is present (fail fast)
            if not gemini_api_key:
                raise Exception(
                    "GEMINI_API_KEY environment variable is required to use Google Gemini nodes. "
                    "Please set GEMINI_API_KEY with your Google AI Studio API key. "
                    "Get your key at: https://aistudio.google.com/apikey"
                )

            # Create endpoint with API key as query parameter
            endpoint = get_gemini_endpoint(model)
            endpoint.query_params = {"key": gemini_api_key}

            # Prepare request data for debugging
            request_data = {
                "model": model.value if isinstance(model, GeminiModel) else model,
                "prompt": prompt[:200] + "..." if len(prompt) > 200 else prompt,
                "has_images": images is not None,
                "has_audio": audio is not None,
                "has_video": video is not None,
                "has_files": files is not None,
                "parts_count": len(parts),
                "seed": seed,
            }

            model_name = model.value if isinstance(model, GeminiModel) else model
            print(f"[EmProps GeminiNode] Making API call with model={model_name}, parts_count={len(parts)}")

            # Execute request with retry mechanism (2 attempts total)
            max_attempts = 2
            response = None
            for attempt in range(1, max_attempts + 1):
                print(f"[EmProps GeminiNode] Attempt {attempt}/{max_attempts}")

                response = await SynchronousOperation(
                    endpoint=endpoint,
                    request=GeminiGenerateContentRequest(
                        contents=[
                            GeminiContent(
                                role="user",
                                parts=parts,
                            )
                        ]
                    ),
                    api_base=GEMINI_BASE_ENDPOINT,
                    comfy_api_key=gemini_api_key,
                ).execute()

                # Check if we got a valid response with candidates
                if response and response.candidates and len(response.candidates) > 0:
                    print(f"[EmProps GeminiNode] Success on attempt {attempt}")
                    break
                else:
                    print(f"[EmProps GeminiNode] Empty response on attempt {attempt}, retrying...")
                    if attempt == max_attempts:
                        print(f"[EmProps GeminiNode] All {max_attempts} attempts failed")

            print(f"[EmProps GeminiNode] Received response, processing...")

            # Get result output
            output_text = get_text_from_response(response)

            if unique_id and output_text:
                # Not a true chat history like the OpenAI Chat node. It is emulated so the frontend can show a copy button.
                render_spec = {
                    "node_id": unique_id,
                    "component": "ChatHistoryWidget",
                    "props": {
                        "history": json.dumps(
                            [
                                {
                                    "prompt": prompt,
                                    "response": output_text,
                                    "response_id": str(uuid.uuid4()),
                                    "timestamp": time.time(),
                                }
                            ]
                        ),
                    },
                }
                PromptServer.instance.send_sync(
                    "display_component",
                    render_spec,
                )

            print(f"[EmProps GeminiNode] Success! Output length: {len(output_text) if output_text else 0} chars")
            return (output_text or "Empty response from Gemini model...",)

        except Exception as e:
            # Create detailed error information
            error_details = create_error_details(
                error=e,
                response=response,
                request_data=request_data,
            )

            # Log detailed error to console
            log_error_details(error_details, node_name="GeminiNode")

            # Create user-friendly error message
            user_message = create_user_friendly_error_message(error_details)

            # Re-raise with enhanced message
            raise Exception(user_message) from e


class GeminiInputFiles(ComfyNodeABC):
    """
    Loads and formats input files for use with the Gemini API.

    This node allows users to include text (.txt) and PDF (.pdf) files as input
    context for the Gemini model. Files are converted to the appropriate format
    required by the API and can be chained together to include multiple files
    in a single request.
    """

    @classmethod
    def INPUT_TYPES(cls) -> InputTypeDict:
        """
        For details about the supported file input types, see:
        https://cloud.google.com/vertex-ai/generative-ai/docs/model-reference/inference
        """
        input_dir = folder_paths.get_input_directory()
        input_files = [
            f
            for f in os.scandir(input_dir)
            if f.is_file()
            and (f.name.endswith(".txt") or f.name.endswith(".pdf"))
            and f.stat().st_size < GEMINI_MAX_INPUT_FILE_SIZE
        ]
        input_files = sorted(input_files, key=lambda x: x.name)
        input_files = [f.name for f in input_files]
        return {
            "required": {
                "file": (
                    IO.COMBO,
                    {
                        "tooltip": "Input files to include as context for the model. Only accepts text (.txt) and PDF (.pdf) files for now.",
                        "options": input_files,
                        "default": input_files[0] if input_files else None,
                    },
                ),
            },
            "optional": {
                "GEMINI_INPUT_FILES": (
                    "GEMINI_INPUT_FILES",
                    {
                        "tooltip": "An optional additional file(s) to batch together with the file loaded from this node. Allows chaining of input files so that a single message can include multiple input files.",
                        "default": None,
                    },
                ),
            },
        }

    DESCRIPTION = "Loads and prepares input files to include as inputs for Gemini LLM nodes. The files will be read by the Gemini model when generating a response. The contents of the text file count toward the token limit. 🛈 TIP: Can be chained together with other Gemini Input File nodes."
    RETURN_TYPES = ("GEMINI_INPUT_FILES",)
    FUNCTION = "prepare_files"
    CATEGORY = "api node/text/Gemini"

    def create_file_part(self, file_path: str) -> GeminiPart:
        mime_type = (
            GeminiMimeType.application_pdf
            if file_path.endswith(".pdf")
            else GeminiMimeType.text_plain
        )
        # Use base64 string directly, not the data URI
        with open(file_path, "rb") as f:
            file_content = f.read()
        base64_str = base64.b64encode(file_content).decode("utf-8")

        return GeminiPart(
            inlineData=GeminiInlineData(
                mimeType=mime_type,
                data=base64_str,
            )
        )

    def prepare_files(
        self, file: str, GEMINI_INPUT_FILES: list[GeminiPart] = []
    ) -> tuple[list[GeminiPart]]:
        """
        Loads and formats input files for Gemini API.
        """
        file_path = folder_paths.get_annotated_filepath(file)
        input_file_content = self.create_file_part(file_path)
        files = [input_file_content] + GEMINI_INPUT_FILES
        return (files,)


class GeminiImage(ComfyNodeABC):
    """
    Node to generate text and image responses from a Gemini model.

    This node allows users to interact with Google's Gemini AI models, providing
    multimodal inputs (text, images, files) to generate coherent
    text and image responses. The node works with the latest Gemini models, handling the
    API communication and response parsing.
    """
    @classmethod
    def INPUT_TYPES(cls) -> InputTypeDict:
        return {
            "required": {
                "prompt": (
                    IO.STRING,
                    {
                        "multiline": True,
                        "default": "",
                        "tooltip": "Text prompt for generation",
                    },
                ),
                "model": (
                    IO.COMBO,
                    {
                        "tooltip": "The Gemini model to use for generating responses.",
                        "options": [model.value for model in GeminiImageModel],
                        "default": GeminiImageModel.gemini_2_5_flash_image.value,
                    },
                ),
                "seed": (
                    IO.INT,
                    {
                        "default": 42,
                        "min": 0,
                        "max": 0xFFFFFFFFFFFFFFFF,
                        "control_after_generate": True,
                        "tooltip": "When seed is fixed to a specific value, the model makes a best effort to provide the same response for repeated requests. Deterministic output isn't guaranteed. Also, changing the model or parameter settings, such as the temperature, can cause variations in the response even when you use the same seed value. By default, a random seed value is used.",
                    },
                ),
            },
            "optional": {
                "images": (
                    IO.IMAGE,
                    {
                        "default": None,
                        "tooltip": "Optional image(s) to use as context for the model. To include multiple images, you can use the Batch Images node.",
                    },
                ),
                "files": (
                    "GEMINI_INPUT_FILES",
                    {
                        "default": None,
                        "tooltip": "Optional file(s) to use as context for the model. Accepts inputs from the Gemini Generate Content Input Files node.",
                    },
                ),
                # TODO: later we can add this parameter later
                # "n": (
                #     IO.INT,
                #     {
                #         "default": 1,
                #         "min": 1,
                #         "max": 8,
                #         "step": 1,
                #         "display": "number",
                #         "tooltip": "How many images to generate",
                #     },
                # ),
                "aspect_ratio": (
                    IO.COMBO,
                    {
                        "tooltip": "Defaults to matching the output image size to that of your input image, or otherwise generates 1:1 squares.",
                        "options": ["auto", "1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"],
                        "default": "auto",
                    },
                ),
            },
            "hidden": {
                "gemini_api_key": "GEMINI_API_KEY",
                "unique_id": "UNIQUE_ID",
            },
        }

    RETURN_TYPES = (IO.IMAGE, IO.STRING)
    FUNCTION = "api_call"
    CATEGORY = "api node/image/Gemini"
    DESCRIPTION = "Edit images synchronously via Google API."
    API_NODE = True

    async def api_call(
        self,
        prompt: str,
        model: GeminiImageModel,
        seed: int,
        images: Optional[IO.IMAGE] = None,
        files: Optional[list[GeminiPart]] = None,
        n=1,
        aspect_ratio: str = "auto",
        unique_id: Optional[str] = None,
        gemini_api_key: Optional[str] = None,
    ):
        response = None
        request_data = None

        try:
            validate_string(prompt, strip_whitespace=True, min_length=1)
            parts: list[GeminiPart] = [create_text_part(prompt)]

            if not aspect_ratio:
                aspect_ratio = "auto"  # for backward compatability with old workflows; to-do remove this in December
            image_config = GeminiImageConfig(aspectRatio=aspect_ratio)

            if images is not None:
                image_parts = create_image_parts(images)
                parts.extend(image_parts)
            if files is not None:
                parts.extend(files)

            # WORKAROUND: ComfyUI's hidden parameter injection is broken, read directly from os.environ
            import os
            gemini_api_key = os.environ.get('GEMINI_API_KEY')

            # Validate API key is present (fail fast)
            if not gemini_api_key:
                raise Exception(
                    "GEMINI_API_KEY environment variable is required to use Google Gemini nodes. "
                    "Please set GEMINI_API_KEY with your Google AI Studio API key. "
                    "Get your key at: https://aistudio.google.com/apikey"
                )

            # Create endpoint with API key as query parameter
            endpoint = get_gemini_image_endpoint(model)
            endpoint.query_params = {"key": gemini_api_key}

            # Prepare request data for debugging
            request_data = {
                "model": model.value if isinstance(model, GeminiImageModel) else model,
                "prompt": prompt[:200] + "..." if len(prompt) > 200 else prompt,
                "has_images": images is not None,
                "has_files": files is not None,
                "parts_count": len(parts),
                "seed": seed,
                "aspect_ratio": aspect_ratio,
            }

            model_name = model.value if isinstance(model, GeminiImageModel) else model
            print(f"[EmProps GeminiImageNode] Making API call with model={model_name}, parts_count={len(parts)}, aspect_ratio={aspect_ratio}")

            # Execute request with retry mechanism (2 attempts total)
            max_attempts = 2
            response = None
            for attempt in range(1, max_attempts + 1):
                print(f"[EmProps GeminiImageNode] Attempt {attempt}/{max_attempts}")

                response = await SynchronousOperation(
                    endpoint=endpoint,
                    request=GeminiImageGenerateContentRequest(
                        contents=[
                            GeminiContent(
                                role="user",
                                parts=parts,
                            ),
                        ],
                        generationConfig=GeminiImageGenerationConfig(
                            responseModalities=["TEXT","IMAGE"],
                            imageConfig=None if aspect_ratio == "auto" else image_config,
                        )
                    ),
                    api_base=GEMINI_BASE_ENDPOINT,
                    comfy_api_key=gemini_api_key,
                ).execute()

                # Check if we got a valid response with candidates
                if response and response.candidates and len(response.candidates) > 0:
                    print(f"[EmProps GeminiImageNode] Success on attempt {attempt}")
                    break
                else:
                    print(f"[EmProps GeminiImageNode] Empty response on attempt {attempt}, retrying...")
                    if attempt == max_attempts:
                        print(f"[EmProps GeminiImageNode] All {max_attempts} attempts failed")

            print(f"[EmProps GeminiImageNode] Received response, processing...")

            output_image = get_image_from_response(response)
            output_text = get_text_from_response(response)

            if unique_id and output_text:
                # Not a true chat history like the OpenAI Chat node. It is emulated so the frontend can show a copy button.
                render_spec = {
                    "node_id": unique_id,
                    "component": "ChatHistoryWidget",
                    "props": {
                        "history": json.dumps(
                            [
                                {
                                    "prompt": prompt,
                                    "response": output_text,
                                    "response_id": str(uuid.uuid4()),
                                    "timestamp": time.time(),
                                }
                            ]
                        ),
                    },
                }
                PromptServer.instance.send_sync(
                    "display_component",
                    render_spec,
                )

            output_text = output_text or "Empty response from Gemini model..."
            print(f"[EmProps GeminiImageNode] Success! Text length: {len(output_text)} chars, Image shape: {output_image.shape if output_image is not None else 'None'}")
            return (output_image, output_text,)

        except Exception as e:
            # Create detailed error information
            error_details = create_error_details(
                error=e,
                response=response,
                request_data=request_data,
            )

            # Log detailed error to console
            log_error_details(error_details, node_name="GeminiImageNode")

            # Create user-friendly error message
            user_message = create_user_friendly_error_message(error_details)

            # Re-raise with enhanced message
            raise Exception(user_message) from e


NODE_CLASS_MAPPINGS = {
    "GeminiNode": GeminiNode,
    "GeminiImageNode": GeminiImage,
    "GeminiInputFiles": GeminiInputFiles,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "GeminiNode": "Google Gemini",
    "GeminiImageNode": "Google Gemini Image",
    "GeminiInputFiles": "Gemini Input Files",
}
