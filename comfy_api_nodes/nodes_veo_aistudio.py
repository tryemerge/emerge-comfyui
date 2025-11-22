"""
Veo Video Generation Nodes (Google AI Studio / Gemini API)

Uses Google AI Studio backend with GEMINI_API_KEY.
Bypasses ComfyUI's proxy for direct API access.
"""

import asyncio
import base64
from io import BytesIO

import aiohttp
from typing_extensions import override

from comfy_api.input_impl.video_types import VideoFromFile
from comfy_api.latest import IO, ComfyExtension
from comfy_api_nodes.util import (
    tensor_to_base64_string,
)
from comfy_api_nodes.gemini_auth import (
    is_ai_studio_configured,
    GEMINI_API_KEY,
)

AVERAGE_DURATION_VIDEO_GEN = 120  # AI Studio can take longer

# Veo model mapping (display name -> actual model ID)
MODELS_MAP = {
    "veo-2.0-generate-001": "veo-2.0-generate-001",
    "veo-3.1-generate": "veo-3.1-generate-preview",
    "veo-3.1-fast-generate": "veo-3.1-fast-generate-preview",
    "veo-3.0-generate-001": "veo-3.0-generate-001",
    "veo-3.0-fast-generate-001": "veo-3.0-fast-generate-001",
}

# AI Studio base URL
AI_STUDIO_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

# Log configuration status at module load
if is_ai_studio_configured():
    print("[EmProps Veo AI Studio] AI Studio configured")
else:
    print("[EmProps Veo AI Studio] AI Studio not configured - set GEMINI_API_KEY")


async def download_video_with_api_key(url: str, api_key: str) -> VideoFromFile:
    """
    Download video from AI Studio URL with API key authentication.

    AI Studio returns URLs like:
    https://generativelanguage.googleapis.com/v1beta/files/xxx:download?alt=media

    These require the API key to be passed as a query parameter or header.
    """
    import tempfile
    import os

    # Add API key to the URL
    separator = "&" if "?" in url else "?"
    authenticated_url = f"{url}{separator}key={api_key}"

    print(f"[EmProps Veo AI Studio] Downloading video from authenticated URL")

    async with aiohttp.ClientSession() as session:
        async with session.get(authenticated_url) as response:
            if response.status != 200:
                error_text = await response.text()
                raise Exception(f"Failed to download video (status {response.status}): {error_text}")

            video_data = await response.read()
            print(f"[EmProps Veo AI Studio] Downloaded {len(video_data)} bytes")

            return VideoFromFile(BytesIO(video_data))


class VeoAIStudioVideoGenerationNode(IO.ComfyNode):
    """
    Generates videos from text prompts using Google's Veo 2 API via AI Studio.

    Requires GEMINI_API_KEY environment variable.
    Get your API key at: https://aistudio.google.com/apikey
    """

    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="VeoAIStudioVideoGenerationNode",
            display_name="Veo 2 Video (AI Studio)",
            category="api node/video/Veo",
            description="Generates videos using Google's Veo 2 API via AI Studio (requires GEMINI_API_KEY)",
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
        seed=0,
        image=None,
        model="veo-2.0-generate-001",
    ):
        if not is_ai_studio_configured():
            raise ValueError(
                "AI Studio not configured. Please set GEMINI_API_KEY environment variable.\n"
                "Get your API key at: https://aistudio.google.com/apikey"
            )

        model_id = MODELS_MAP.get(model, model)
        print(f"[EmProps Veo AI Studio] Generating video with model={model_id}")

        # Prepare the instances for the request
        instances = []
        instance = {"prompt": prompt}

        # Add image if provided
        if image is not None:
            image_base64 = tensor_to_base64_string(image)
            if image_base64:
                instance["image"] = {"bytesBase64Encoded": image_base64, "mimeType": "image/png"}

        instances.append(instance)

        # Create parameters dictionary for AI Studio
        # AI Studio has limited parameter support compared to Vertex AI
        parameters = {
            "aspectRatio": aspect_ratio,
            "durationSeconds": duration_seconds,
        }

        # Add optional parameters if provided
        if negative_prompt:
            parameters["negativePrompt"] = negative_prompt
        if seed > 0:
            parameters["seed"] = seed

        # Build request payload
        request_data = {
            "instances": instances,
            "parameters": parameters,
        }

        # AI Studio uses predictLongRunning endpoint
        generate_url = f"{AI_STUDIO_BASE_URL}/models/{model_id}:predictLongRunning"
        print(f"[EmProps Veo AI Studio] Calling API: {generate_url}")

        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": GEMINI_API_KEY,
        }

        async with aiohttp.ClientSession() as session:
            # Start the video generation
            async with session.post(generate_url, json=request_data, headers=headers) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise Exception(f"Veo API error (status {response.status}): {error_text}")

                initial_response = await response.json()

            operation_name = initial_response.get("name")
            if not operation_name:
                raise Exception(f"No operation name in response: {initial_response}")

            print(f"[EmProps Veo AI Studio] Got operation: {operation_name}")

            # Poll for completion using GET request
            # AI Studio polling uses: GET https://generativelanguage.googleapis.com/v1beta/{operation_name}
            poll_url = f"{AI_STUDIO_BASE_URL}/{operation_name}"
            print(f"[EmProps Veo AI Studio] Polling: {poll_url}")

            max_attempts = 120  # 10 minutes at 5 second intervals
            poll_interval = 5.0

            for attempt in range(max_attempts):
                async with session.get(poll_url, headers=headers) as poll_response:
                    if poll_response.status != 200:
                        error_text = await poll_response.text()
                        raise Exception(f"Polling error (status {poll_response.status}): {error_text}")

                    poll_data = await poll_response.json()

                is_done = poll_data.get("done", False)
                print(f"[EmProps Veo AI Studio] Poll attempt {attempt + 1}/{max_attempts}, done={is_done}")

                if is_done:
                    break

                await asyncio.sleep(poll_interval)
            else:
                raise Exception(f"Polling timed out after {max_attempts * poll_interval} seconds")

            # Check for errors in the final response
            if "error" in poll_data:
                error = poll_data["error"]
                error_msg = error.get("message", "Unknown error")
                error_code = error.get("code", "unknown")
                raise Exception(f"Veo API error: {error_msg} (code: {error_code})")

            # Log the full response for debugging
            print(f"[EmProps Veo AI Studio] Poll response keys: {poll_data.keys()}")
            print(f"[EmProps Veo AI Studio] Full poll response: {poll_data}")

            # Extract response data - AI Studio nests differently than Vertex AI
            response_data = poll_data.get("response", {})
            print(f"[EmProps Veo AI Studio] Response data: {response_data}")

            # AI Studio structure:
            # response.generateVideoResponse.generatedSamples[0].video.uri
            generate_video_response = response_data.get("generateVideoResponse", {})
            print(f"[EmProps Veo AI Studio] generateVideoResponse: {generate_video_response}")
            generated_samples = generate_video_response.get("generatedSamples", [])
            print(f"[EmProps Veo AI Studio] generatedSamples count: {len(generated_samples)}")

            if not generated_samples:
                # Check for RAI filtered content
                rai_filtered_count = response_data.get("raiMediaFilteredCount", 0)
                if rai_filtered_count > 0:
                    reasons = response_data.get("raiMediaFilteredReasons", [])
                    if reasons:
                        error_message = f"Content filtered by Google's Responsible AI practices: {reasons[0]} ({rai_filtered_count} videos filtered.)"
                    else:
                        error_message = f"Content filtered by Google's Responsible AI practices ({rai_filtered_count} videos filtered.)"
                    raise Exception(error_message)
                raise Exception("Video generation completed but no video was returned")

            # Get the first generated sample
            sample = generated_samples[0]
            video = sample.get("video", {})

            # AI Studio returns video URI, not base64
            video_uri = video.get("uri")
            if video_uri:
                print(f"[EmProps Veo AI Studio] Video received as URI: {video_uri}")
                # Download the video using the API key for authentication
                return IO.NodeOutput(await download_video_with_api_key(video_uri, GEMINI_API_KEY))

            raise Exception("Video returned but no URI was provided")


class Veo3AIStudioVideoGenerationNode(IO.ComfyNode):
    """
    Generates videos from text prompts using Google's Veo 3 API via AI Studio.

    Supported models:
    - veo-3.1-generate (preview)
    - veo-3.1-fast-generate (preview)
    - veo-3.0-generate-001
    - veo-3.0-fast-generate-001

    Requires GEMINI_API_KEY environment variable.
    """

    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="Veo3AIStudioVideoGenerationNode",
            display_name="Veo 3 Video (AI Studio)",
            category="api node/video/Veo",
            description="Generates videos using Google's Veo 3 API via AI Studio (requires GEMINI_API_KEY)",
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
        duration_seconds=8,
        seed=0,
        image=None,
        model="veo-3.0-generate-001",
    ):
        # Reuse the Veo 2 execute logic - AI Studio doesn't support extra params anyway
        return await VeoAIStudioVideoGenerationNode.execute(
            prompt=prompt,
            aspect_ratio=aspect_ratio,
            negative_prompt=negative_prompt,
            duration_seconds=duration_seconds,
            seed=seed,
            image=image,
            model=model,
        )


class VeoAIStudioExtension(ComfyExtension):
    @override
    async def get_node_list(self) -> list[type[IO.ComfyNode]]:
        # Always register nodes - they'll show auth errors if not configured
        return [
            VeoAIStudioVideoGenerationNode,
            Veo3AIStudioVideoGenerationNode,
        ]


async def comfy_entrypoint() -> VeoAIStudioExtension:
    return VeoAIStudioExtension()
