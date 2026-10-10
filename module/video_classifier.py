"""Compatibility entry point; new code uses module.resource_analysis."""

from module.resource_analysis import analyze_resource, category_to_dir


async def classify_video(
    video_path,
    api_key,
    api_base="https://api.openai.com/v1",
    model="gpt-4o-mini",
    max_frames=4,
    timeout=60,
    text_hints=None,
    transcribe=False,
    progress_callback=None,
):
    return await analyze_resource(
        video_path,
        api_key,
        api_base,
        model,
        max_frames,
        timeout,
        text_hints,
        transcribe,
        progress_callback,
        media_type="video",
    )
