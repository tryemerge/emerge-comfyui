"""
Veo Video Generation Nodes (Vertex AI Direct API Access)

Uses Vertex AI backend with GCP service account credentials.
Bypasses ComfyUI's proxy for direct API access.
"""

import base64
from io import BytesIO

from typing_extensions import override

from comfy_api.input_impl.video_types import VideoFromFile
from comfy_api.latest import IO, ComfyExtension
from comfy_api_nodes.apis.veo_api import (
    VeoGenVidPollRequest,
    VeoGenVidPollResponse,
    VeoGenVidRequest,
    VeoGenVidResponse,
)
from comfy_api_nodes.util import (
    download_url_to_video_output,
    poll_op,
    sync_op,
    tensor_to_base64_string,
)
from comfy_api_nodes.gemini_auth import (
    get_veo_endpoint,
    is_vertex_ai_configured,
    GCP_PROJECT_ID,
    GCP_REGION,
)

AVERAGE_DURATION_VIDEO_GEN = 32

# Veo model mapping (display name -> actual model ID)
MODELS_MAP = {
    "veo-2.0-generate-001": "veo-2.0-generate-001",
    "veo-3.1-generate": "veo-3.1-generate-preview",
    "veo-3.1-fast-generate": "veo-3.1-fast-generate-preview",
    "veo-3.0-generate-001": "veo-3.0-generate-001",
    "veo-3.0-fast-generate-001": "veo-3.0-fast-generate-001",
}

# Log configuration status at module load
if is_vertex_ai_configured():
    print(f"[EmProps Veo Vertex] Vertex AI configured (project={GCP_PROJECT_ID}, region={GCP_REGION})")
else:
    print("[EmProps Veo Vertex] Vertex AI not configured - set GCP_PROJECT_ID and credentials")


class VeoVertexVideoGenerationNode(IO.ComfyNode):
    """
    Generates videos from text prompts using Google's Veo 2 API via Vertex AI.

    Requires GCP service account credentials configured via:
    - GOOGLE_APPLICATION_CREDENTIALS (path to service account key)
    - GCP_PROJECT_ID (your GCP project ID)
    - GCP_REGION (optional, defaults to us-central1)
    """

    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="VeoVertexVideoGenerationNode",
            display_name="Veo 2 Video (Vertex AI)",
            category="api node/video/Veo",
            description="Generates videos using Google's Veo 2 API via Vertex AI (requires GCP credentials)",
            inputs=[
                IO.String.Input(
                    "prompt",
                    multiline=True,
                    default="",
                    tooltip="Text description of the video",
                ),
                IO.Combo.Input(
                    "aspect_ratio",
                    options=["16:9", "9:16"],
                    default="16:9",
                    tooltip="Aspect ratio of the output video",
                ),
                IO.String.Input(
                    "negative_prompt",
                    multiline=True,
                    default="",
                    tooltip="Negative text prompt to guide what to avoid in the video",
                    optional=True,
                ),
                IO.Int.Input(
                    "duration_seconds",
                    default=5,
                    min=5,
                    max=8,
                    step=1,
                    display_mode=IO.NumberDisplay.number,
                    tooltip="Duration of the output video in seconds",
                    optional=True,
                ),
                IO.Boolean.Input(
                    "enhance_prompt",
                    default=True,
                    tooltip="Whether to enhance the prompt with AI assistance",
                    optional=True,
                ),
                IO.Combo.Input(
                    "person_generation",
                    options=["ALLOW", "BLOCK"],
                    default="ALLOW",
                    tooltip="Whether to allow generating people in the video",
                    optional=True,
                ),
                IO.Int.Input(
                    "seed",
                    default=0,
                    min=0,
                    max=0xFFFFFFFF,
                    step=1,
                    display_mode=IO.NumberDisplay.number,
                    control_after_generate=True,
                    tooltip="Seed for video generation (0 for random)",
                    optional=True,
                ),
                IO.Image.Input(
                    "image",
                    tooltip="Optional reference image to guide video generation",
                    optional=True,
                ),
                IO.Combo.Input(
                    "model",
                    options=["veo-2.0-generate-001"],
                    default="veo-2.0-generate-001",
                    tooltip="Veo 2 model to use for video generation",
                    optional=True,
                ),
            ],
            outputs=[
                IO.Video.Output(),
            ],
            hidden=[
                IO.Hidden.unique_id,
            ],
            is_api_node=True,
        )

    @classmethod
    async def execute(
        cls,
        prompt,
        aspect_ratio="16:9",
        negative_prompt="",
        duration_seconds=5,
        enhance_prompt=True,
        person_generation="ALLOW",
        seed=0,
        image=None,
        model="veo-2.0-generate-001",
        generate_audio=False,
    ):
        model_id = MODELS_MAP.get(model, model)
        print(f"[EmProps Veo Vertex] Generating video with model={model_id}")

        # Prepare the instances for the request
        instances = []
        instance = {"prompt": prompt}

        # Add image if provided
        if image is not None:
            image_base64 = tensor_to_base64_string(image)
            if image_base64:
                instance["image"] = {"bytesBase64Encoded": image_base64, "mimeType": "image/png"}

        instances.append(instance)

        # Create parameters dictionary for Vertex AI
        parameters = {
            "aspectRatio": aspect_ratio,
            "durationSeconds": duration_seconds,
            "personGeneration": person_generation,
        }

        # Add optional parameters if provided
        if negative_prompt:
            parameters["negativePrompt"] = negative_prompt
        if seed > 0:
            parameters["seed"] = seed

        # Veo 2 supports enhancePrompt on Vertex AI
        if model_id.find("veo-2.0") != -1:
            parameters["enhancePrompt"] = enhance_prompt
        # Veo 3 supports generateAudio on Vertex AI
        else:
            parameters["generateAudio"] = generate_audio

        # Get endpoint for video generation
        generate_endpoint = get_veo_endpoint(model_id, "predictLongRunning", "vertex_ai")
        print(f"[EmProps Veo Vertex] Calling API: {generate_endpoint.path}")

        initial_response = await sync_op(
            cls,
            generate_endpoint,
            response_model=VeoGenVidResponse,
            data=VeoGenVidRequest(
                instances=instances,
                parameters=parameters,
            ),
        )

        print(f"[EmProps Veo Vertex] Got operation: {initial_response.name}")

        def status_extractor(response):
            return "completed" if response.done else "pending"

        # Get endpoint for polling
        poll_endpoint = get_veo_endpoint(model_id, "fetchPredictOperation", "vertex_ai")

        poll_response = await poll_op(
            cls,
            poll_endpoint,
            response_model=VeoGenVidPollResponse,
            status_extractor=status_extractor,
            data=VeoGenVidPollRequest(
                operationName=initial_response.name,
            ),
            poll_interval=5.0,
            estimated_duration=AVERAGE_DURATION_VIDEO_GEN,
        )

        # Check for errors
        if poll_response.error:
            raise Exception(f"Veo API error: {poll_response.error.message} (code: {poll_response.error.code})")

        # Check for RAI filtered content
        if (
            hasattr(poll_response.response, "raiMediaFilteredCount")
            and poll_response.response.raiMediaFilteredCount > 0
        ):
            if (
                hasattr(poll_response.response, "raiMediaFilteredReasons")
                and poll_response.response.raiMediaFilteredReasons
            ):
                reason = poll_response.response.raiMediaFilteredReasons[0]
                error_message = f"Content filtered by Google's Responsible AI practices: {reason} ({poll_response.response.raiMediaFilteredCount} videos filtered.)"
            else:
                error_message = f"Content filtered by Google's Responsible AI practices ({poll_response.response.raiMediaFilteredCount} videos filtered.)"
            raise Exception(error_message)

        # Extract video data
        if (
            poll_response.response
            and hasattr(poll_response.response, "videos")
            and poll_response.response.videos
            and len(poll_response.response.videos) > 0
        ):
            video = poll_response.response.videos[0]

            # Check if video is provided as base64 or URL
            if hasattr(video, "bytesBase64Encoded") and video.bytesBase64Encoded:
                print("[EmProps Veo Vertex] Video received as base64")
                return IO.NodeOutput(VideoFromFile(BytesIO(base64.b64decode(video.bytesBase64Encoded))))

            if hasattr(video, "gcsUri") and video.gcsUri:
                print(f"[EmProps Veo Vertex] Video received as GCS URI: {video.gcsUri}")
                return IO.NodeOutput(await download_url_to_video_output(video.gcsUri))

            raise Exception("Video returned but no data or URL was provided")
        raise Exception("Video generation completed but no video was returned")


class Veo3VertexVideoGenerationNode(VeoVertexVideoGenerationNode):
    """
    Generates videos from text prompts using Google's Veo 3 API via Vertex AI.

    Supported models:
    - veo-3.1-generate (preview)
    - veo-3.1-fast-generate (preview)
    - veo-3.0-generate-001
    - veo-3.0-fast-generate-001

    Requires GCP service account credentials.
    """

    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="Veo3VertexVideoGenerationNode",
            display_name="Veo 3 Video (Vertex AI)",
            category="api node/video/Veo",
            description="Generates videos using Google's Veo 3 API via Vertex AI (requires GCP credentials)",
            inputs=[
                IO.String.Input(
                    "prompt",
                    multiline=True,
                    default="",
                    tooltip="Text description of the video",
                ),
                IO.Combo.Input(
                    "aspect_ratio",
                    options=["16:9", "9:16"],
                    default="16:9",
                    tooltip="Aspect ratio of the output video",
                ),
                IO.String.Input(
                    "negative_prompt",
                    multiline=True,
                    default="",
                    tooltip="Negative text prompt to guide what to avoid in the video",
                    optional=True,
                ),
                IO.Int.Input(
                    "duration_seconds",
                    default=8,
                    min=8,
                    max=8,
                    step=1,
                    display_mode=IO.NumberDisplay.number,
                    tooltip="Duration of the output video in seconds (Veo 3 only supports 8 seconds)",
                    optional=True,
                ),
                IO.Combo.Input(
                    "person_generation",
                    options=["ALLOW", "BLOCK"],
                    default="ALLOW",
                    tooltip="Whether to allow generating people in the video",
                    optional=True,
                ),
                IO.Int.Input(
                    "seed",
                    default=0,
                    min=0,
                    max=0xFFFFFFFF,
                    step=1,
                    display_mode=IO.NumberDisplay.number,
                    control_after_generate=True,
                    tooltip="Seed for video generation (0 for random)",
                    optional=True,
                ),
                IO.Image.Input(
                    "image",
                    tooltip="Optional reference image to guide video generation",
                    optional=True,
                ),
                IO.Combo.Input(
                    "model",
                    options=[
                        "veo-3.1-generate",
                        "veo-3.1-fast-generate",
                        "veo-3.0-generate-001",
                        "veo-3.0-fast-generate-001",
                    ],
                    default="veo-3.0-generate-001",
                    tooltip="Veo 3 model to use for video generation",
                    optional=True,
                ),
                IO.Boolean.Input(
                    "generate_audio",
                    default=False,
                    tooltip="Generate audio for the video. Supported by all Veo 3 models.",
                    optional=True,
                ),
            ],
            outputs=[
                IO.Video.Output(),
            ],
            hidden=[
                IO.Hidden.unique_id,
            ],
            is_api_node=True,
        )


class VeoVertexExtension(ComfyExtension):
    @override
    async def get_node_list(self) -> list[type[IO.ComfyNode]]:
        # Always register nodes - they'll show auth errors if not configured
        return [
            VeoVertexVideoGenerationNode,
            Veo3VertexVideoGenerationNode,
        ]


async def comfy_entrypoint() -> VeoVertexExtension:
    return VeoVertexExtension()
