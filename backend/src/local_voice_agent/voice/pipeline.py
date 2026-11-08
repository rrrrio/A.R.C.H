"""Construct the first all-local Pipecat speech conversation pipeline."""

from __future__ import annotations

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.services.kokoro.tts import KokoroTTSService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.whisper.stt import WhisperSTTService
from pipecat.transcriptions.language import Language
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport

from local_voice_agent.voice.models import VoiceModelPaths


def create_pipeline_worker(
    connection: SmallWebRTCConnection,
    paths: VoiceModelPaths,
    llama_endpoint: str,
) -> PipelineWorker:
    """Build one Chinese-first local voice worker for an authenticated peer connection."""
    paths.require_ready()
    transport = SmallWebRTCTransport(
        connection,
        TransportParams(
            audio_in_enabled=True,
            audio_in_sample_rate=16000,
            audio_out_enabled=True,
            audio_out_sample_rate=24000,
        ),
    )
    vad = VADProcessor(vad_analyzer=SileroVADAnalyzer(sample_rate=16000))
    stt = WhisperSTTService(
        device="cuda",
        compute_type="float16",
        settings=WhisperSTTService.Settings(model=str(paths.asr_model), language=Language.ZH),
    )
    llm = OpenAILLMService(
        api_key="local-not-used",
        base_url=llama_endpoint,
        settings=OpenAILLMService.Settings(model="qwen3-8b-q4-k-m"),
    )
    tts = KokoroTTSService(
        model_path=str(paths.tts_model),
        voices_path=str(paths.tts_voices),
        settings=KokoroTTSService.Settings(voice="zf_001", language=Language.ZH, speed=1.0),
    )
    context = LLMContext(
        messages=[
            {
                "role": "system",
                "content": "你是本机语音助手。用简洁的中文回答，避免展示推理过程。",
            }
        ]
    )
    context_aggregator = LLMContextAggregatorPair(context)
    pipeline = Pipeline(
        [
            transport.input(),
            vad,
            stt,
            context_aggregator.user(),
            llm,
            tts,
            transport.output(),
            context_aggregator.assistant(),
        ]
    )
    return PipelineWorker(
        pipeline,
        params=PipelineParams(audio_in_sample_rate=16000, audio_out_sample_rate=24000),
    )
