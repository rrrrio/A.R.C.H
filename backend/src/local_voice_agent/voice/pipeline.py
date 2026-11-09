"""Construct the first all-local Pipecat speech conversation pipeline."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import WebSocket
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.services.kokoro.tts import KokoroTTSService
from pipecat.services.whisper.stt import WhisperSTTService
from pipecat.transcriptions.language import Language
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams, FastAPIWebsocketTransport

from local_voice_agent.agent.llama import LlamaCppAgentCore
from local_voice_agent.memory.extractor import LlamaCppMemoryCandidateExtractor
from local_voice_agent.memory.store import MemoryStore
from local_voice_agent.observability.metrics import LatencyStage, LatencyTracker
from local_voice_agent.rag.retriever import Retriever
from local_voice_agent.tools.gateway import ToolGateway
from local_voice_agent.voice.agent_processor import PipecatAgentProcessor
from local_voice_agent.voice.events import VoiceEventProcessor
from local_voice_agent.voice.input_ready import InputReadyProcessor
from local_voice_agent.voice.metrics import VoiceLatencyProcessor
from local_voice_agent.voice.models import VoiceModelPaths
from local_voice_agent.voice.pipecat_output import PipecatOutputMuxProcessor
from local_voice_agent.voice.websocket import BrowserPCMSerializer

LAN_VAD_PARAMS = VADParams(confidence=0.6, min_volume=0.25)


def create_pipeline_worker(
    connection: SmallWebRTCConnection,
    paths: VoiceModelPaths,
    llama_endpoint: str,
    memory_store: MemoryStore | None = None,
    retriever: Retriever | None = None,
    tool_gateway: ToolGateway | None = None,
    on_agent_processor: Callable[[PipecatAgentProcessor], None] | None = None,
) -> PipelineWorker:
    """Build one Chinese-first local voice worker for an authenticated peer connection."""
    transport = SmallWebRTCTransport(
        connection,
        TransportParams(
            audio_in_enabled=True,
            audio_in_sample_rate=16000,
            audio_out_enabled=True,
            audio_out_sample_rate=24000,
        ),
    )
    return _create_pipeline_worker(
        transport, connection.send_app_message, paths, llama_endpoint, memory_store, retriever,
        tool_gateway, on_agent_processor,
    )


def create_websocket_pipeline_worker(
    websocket: WebSocket,
    send_event: Callable[[dict[str, str | int]], None],
    paths: VoiceModelPaths,
    llama_endpoint: str,
    memory_store: MemoryStore | None = None,
    retriever: Retriever | None = None,
    tool_gateway: ToolGateway | None = None,
    on_agent_processor: Callable[[PipecatAgentProcessor], None] | None = None,
) -> PipelineWorker:
    """Build the same worker over authenticated same-origin PCM WebSocket media."""
    transport = FastAPIWebsocketTransport(
        websocket,
        FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_in_sample_rate=16000,
            audio_out_enabled=True,
            audio_out_sample_rate=24000,
            serializer=BrowserPCMSerializer(),
            allowed_origins=[],
        ),
    )
    return _create_pipeline_worker(
        transport, send_event, paths, llama_endpoint, memory_store, retriever, tool_gateway,
        on_agent_processor,
    )


def _create_pipeline_worker(
    transport: BaseTransport,
    send_event: Callable[[dict[str, str | int]], None],
    paths: VoiceModelPaths,
    llama_endpoint: str,
    memory_store: MemoryStore | None,
    retriever: Retriever | None,
    tool_gateway: ToolGateway | None,
    on_agent_processor: Callable[[PipecatAgentProcessor], None] | None,
) -> PipelineWorker:
    """Assemble processors independent of the authenticated browser transport."""
    paths.require_ready()
    # Remote LAN microphones can be quieter than the workstation default input.
    vad = VADProcessor(
        vad_analyzer=SileroVADAnalyzer(
            sample_rate=16000,
            params=LAN_VAD_PARAMS,
        )
    )
    stt = WhisperSTTService(
        device="cuda",
        compute_type="float16",
        settings=WhisperSTTService.Settings(model=str(paths.asr_model), language=Language.ZH),
    )
    agent = PipecatAgentProcessor(
        LlamaCppAgentCore(llama_endpoint),
        memory_store=memory_store,
        candidate_extractor=LlamaCppMemoryCandidateExtractor(llama_endpoint),
        retriever=retriever,
        tool_gateway=tool_gateway,
    )
    if on_agent_processor is not None:
        on_agent_processor(agent)
    tts = KokoroTTSService(
        model_path=str(paths.tts_model),
        voices_path=str(paths.tts_voices),
        settings=KokoroTTSService.Settings(voice="zf_001", language=Language.ZH, speed=1.0),
    )
    user_events = VoiceEventProcessor(send_event)
    assistant_events = VoiceEventProcessor(send_event)
    output_events = VoiceEventProcessor(send_event)
    tracker = LatencyTracker()
    input_latency = VoiceLatencyProcessor(
        send_event,
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
        send_event, tracker, stages=frozenset({LatencyStage.ASR_FINAL})
    )
    llm_latency = VoiceLatencyProcessor(
        send_event, tracker, stages=frozenset({LatencyStage.LLM_FIRST_TOKEN})
    )
    tts_latency = VoiceLatencyProcessor(
        send_event, tracker, stages=frozenset({LatencyStage.TTS_FIRST_BYTE})
    )
    output_latency = VoiceLatencyProcessor(
        send_event, tracker, stages=frozenset({LatencyStage.PLAYBACK_FIRST_AUDIO})
    )
    output_mux = PipecatOutputMuxProcessor(output_sample_rate=24000)
    pipeline = Pipeline(
        [
            transport.input(),
            InputReadyProcessor(send_event),
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
