"""Construct the first all-local Pipecat speech conversation pipeline."""

from __future__ import annotations

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.services.kokoro.tts import KokoroTTSService
from pipecat.services.whisper.stt import WhisperSTTService
from pipecat.transcriptions.language import Language
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport

from local_voice_agent.agent.llama import LlamaCppAgentCore
from local_voice_agent.memory.store import MemoryStore
from local_voice_agent.observability.metrics import LatencyStage, LatencyTracker
from local_voice_agent.voice.agent_processor import PipecatAgentProcessor
from local_voice_agent.voice.events import VoiceEventProcessor
from local_voice_agent.voice.metrics import VoiceLatencyProcessor
from local_voice_agent.voice.models import VoiceModelPaths
from local_voice_agent.voice.pipecat_output import PipecatOutputMuxProcessor


def create_pipeline_worker(
    connection: SmallWebRTCConnection,
    paths: VoiceModelPaths,
    llama_endpoint: str,
    memory_store: MemoryStore | None = None,
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
    agent = PipecatAgentProcessor(LlamaCppAgentCore(llama_endpoint), memory_store=memory_store)
    tts = KokoroTTSService(
        model_path=str(paths.tts_model),
        voices_path=str(paths.tts_voices),
        settings=KokoroTTSService.Settings(voice="zf_001", language=Language.ZH, speed=1.0),
    )
    user_events = VoiceEventProcessor(connection.send_app_message)
    assistant_events = VoiceEventProcessor(connection.send_app_message)
    output_events = VoiceEventProcessor(connection.send_app_message)
    tracker = LatencyTracker()
    input_latency = VoiceLatencyProcessor(
        connection.send_app_message,
        tracker,
        stages=frozenset(
            {
                LatencyStage.SPEECH_START,
                LatencyStage.SPEECH_END,
                LatencyStage.PLAYBACK_STOPPED,
            }
        ),
    )
    asr_latency = VoiceLatencyProcessor(
        connection.send_app_message, tracker, stages=frozenset({LatencyStage.ASR_FINAL})
    )
    llm_latency = VoiceLatencyProcessor(
        connection.send_app_message, tracker, stages=frozenset({LatencyStage.LLM_FIRST_TOKEN})
    )
    tts_latency = VoiceLatencyProcessor(
        connection.send_app_message, tracker, stages=frozenset({LatencyStage.TTS_FIRST_BYTE})
    )
    output_latency = VoiceLatencyProcessor(
        connection.send_app_message, tracker, stages=frozenset({LatencyStage.PLAYBACK_FIRST_AUDIO})
    )
    output_mux = PipecatOutputMuxProcessor(output_sample_rate=24000)
    pipeline = Pipeline(
        [
            transport.input(),
            vad,
            input_latency,
            stt,
            asr_latency,
            user_events,
            agent,
            llm_latency,
            assistant_events,
            tts,
            tts_latency,
            output_mux,
            output_events,
            output_latency,
            transport.output(),
        ]
    )
    return PipelineWorker(
        pipeline,
        params=PipelineParams(audio_in_sample_rate=16000, audio_out_sample_rate=24000),
    )
