"""
TokenCoin Public API Server
============================
Provides an OpenAI-compatible REST API that routes inference requests
to the distributed Ollama mining network.

Endpoints:
  - GET  /v1/models          -> List available models
  - POST /v1/chat/completions -> Chat completion (routed to miners)
  - POST /v1/embeddings      -> Embedding (routed to miners)
  - GET  /v1/health          -> Server health

External users call this API like they would OpenAI. Behind the scenes,
requests are distributed as PoUW jobs to mining nodes via the P2P DHT.
"""

import asyncio
import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, AsyncIterator
from enum import Enum

import aiohttp
from aiohttp import web

from tokencoin.config import CONFIG
from tokencoin.mining.ollama_miner import (
    OllamaManager, OllamaModel, OLLAMA_MODELS, MODEL_REGISTRY,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# API Configuration
# ---------------------------------------------------------------------------

@dataclass
class APIConfig:
    """Configuration for the public API server."""
    host: str = "0.0.0.0"
    port: int = 8080
    max_concurrent_requests: int = 100
    request_timeout_seconds: int = 300
    require_api_key: bool = False
    api_keys: List[str] = field(default_factory=list)
    # How long to wait for a miner to claim a job before timing out
    job_claim_timeout_seconds: int = 30
    # How long to wait for a miner to complete a job
    job_completion_timeout_seconds: int = 240


# ---------------------------------------------------------------------------
# Job Queue (bridges API requests to mining network)
# ---------------------------------------------------------------------------

class JobStatus(Enum):
    """Status of an inference job in the queue."""
    PENDING = "pending"        # Waiting for a miner to claim
    CLAIMED = "claimed"        # A miner has claimed the job
    PROCESSING = "processing"  # Miner is running inference
    COMPLETED = "completed"    # Inference done, result ready
    FAILED = "failed"          # Inference failed
    TIMEOUT = "timeout"        # No miner claimed in time


@dataclass
class APIJob:
    """
    An inference job submitted via the public API.
    Gets distributed to the mining network via DHT gossip.
    """
    job_id: str
    model: str
    prompt: str
    job_type: str  # "chat" or "embedding"
    status: JobStatus = JobStatus.PENDING
    created_at: float = field(default_factory=time.time)
    claimed_by: str = ""  # Miner node_id
    claimed_at: float = 0.0
    result: Optional[Dict[str, Any]] = None
    error: str = ""
    api_key: str = ""
    # Parameters
    max_tokens: int = 128
    temperature: float = 0.0
    seed: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "model": self.model,
            "status": self.status.value,
            "created_at": self.created_at,
            "claimed_by": self.claimed_by,
            "result": self.result,
            "error": self.error,
        }


class JobDistributor:
    """
    Distributes API inference jobs to the mining network.
    Uses the P2P gossip protocol to broadcast jobs and collect results.
    """

    def __init__(self, ollama_manager: OllamaManager, p2p_node=None):
        self.ollama = ollama_manager
        self.p2p = p2p_node  # P2PNode instance for network distribution
        self.jobs: Dict[str, APIJob] = {}
        self._running = False
        self._cleanup_task: Optional[asyncio.Task] = None

    async def start(self):
        """Start the job distributor."""
        self._running = True
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info("Job distributor started")

    async def stop(self):
        """Stop the job distributor."""
        self._running = False
        if self._cleanup_task:
            self._cleanup_task.cancel()
            self._cleanup_task = None
        logger.info("Job distributor stopped")

    async def submit_job(self, job: APIJob) -> APIJob:
        """
        Submit a job to the mining network.
        First tries local mining, then broadcasts to the P2P network.
        """
        self.jobs[job.job_id] = job
        job.status = JobStatus.PENDING

        # 1. Try local mining first (fast path)
        local_task = asyncio.create_task(self._try_local_mining(job))

        # 2. Broadcast to P2P network (if available)
        if self.p2p:
            await self._broadcast_job(job)

        # 3. Wait for result (local or remote)
        try:
            await asyncio.wait_for(
                local_task,
                timeout=CONFIG.ollama.job_timeout_seconds
            )
        except asyncio.TimeoutError:
            # Local mining timed out, but remote might still be working
            pass

        return job

    async def _try_local_mining(self, job: APIJob) -> bool:
        """Try to process the job locally."""
        model = MODEL_REGISTRY.get(job.model)

        if not self.ollama.hardware.can_run_model(model):
            logger.debug(f"Local hardware can't run {job.model}, skipping")
            return False

        logger.info(f"Processing job {job.job_id} locally with {job.model}")
        job.status = JobStatus.PROCESSING
        job.claimed_by = "local"
        job.claimed_at = time.time()

        try:
            if job.job_type == "embedding":
                result = await self.ollama.embed(model, job.prompt)
                if result:
                    job.result = {
                        "object": "list",
                        "data": [{"object": "embedding", "embedding": result, "index": 0}],
                        "model": job.model,
                        "usage": {"prompt_tokens": len(job.prompt.split()), "total_tokens": len(job.prompt.split())},
                    }
                    job.status = JobStatus.COMPLETED
                    return True
            else:
                # Chat completion
                result = await self.ollama.generate(
                    model=model,
                    prompt=job.prompt,
                    options={
                        "num_predict": job.max_tokens,
                        "temperature": job.temperature,
                        "seed": job.seed or int(time.time()),
                    }
                )
                if result:
                    job.result = {
                        "id": f"chatcmpl-{job.job_id}",
                        "object": "chat.completion",
                        "created": int(time.time()),
                        "model": job.model,
                        "choices": [{
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": result.get("response", ""),
                            },
                            "finish_reason": "stop",
                        }],
                        "usage": {
                            "prompt_tokens": result.get("prompt_eval_count", 0),
                            "completion_tokens": result.get("eval_count", 0),
                            "total_tokens": result.get("prompt_eval_count", 0) + result.get("eval_count", 0),
                        },
                    }
                    job.status = JobStatus.COMPLETED
                    return True

        except Exception as e:
            logger.error(f"Local mining failed for job {job.job_id}: {e}")
            job.error = str(e)

        job.status = JobStatus.FAILED
        return False

    async def _broadcast_job(self, job: APIJob):
        """Broadcast a job to the P2P network for remote miners."""
        if not self.p2p:
            return

        job_data = json.dumps({
            "job_id": job.job_id,
            "model": job.model,
            "prompt": job.prompt,
            "job_type": job.job_type,
            "max_tokens": job.max_tokens,
            "temperature": job.temperature,
            "seed": job.seed,
        }).encode()

        await self.p2p.broadcast_job(job.job_id, job_data)
        logger.info(f"Broadcast job {job.job_id} to P2P network")

    def claim_job(self, job_id: str, miner_id: str) -> Optional[APIJob]:
        """A miner claims a job. Returns the job if available."""
        job = self.jobs.get(job_id)
        if job and job.status == JobStatus.PENDING:
            job.status = JobStatus.CLAIMED
            job.claimed_by = miner_id
            job.claimed_at = time.time()
            logger.info(f"Job {job_id} claimed by miner {miner_id[:16]}...")
            return job
        return None

    def complete_job(self, job_id: str, result: Dict[str, Any]) -> bool:
        """Mark a job as completed with a result."""
        job = self.jobs.get(job_id)
        if job:
            job.status = JobStatus.COMPLETED
            job.result = result
            logger.info(f"Job {job_id} completed")
            return True
        return False

    def fail_job(self, job_id: str, error: str = ""):
        """Mark a job as failed."""
        job = self.jobs.get(job_id)
        if job:
            job.status = JobStatus.FAILED
            job.error = error
            logger.warning(f"Job {job_id} failed: {error}")

    def get_job(self, job_id: str) -> Optional[APIJob]:
        """Get a job by ID."""
        return self.jobs.get(job_id)

    async def wait_for_result(self, job_id: str,
                               timeout: float = 120) -> Optional[APIJob]:
        """Wait for a job to complete."""
        job = self.jobs.get(job_id)
        if not job:
            return None

        start = time.time()
        while time.time() - start < timeout:
            if job.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.TIMEOUT):
                return job
            await asyncio.sleep(0.5)

        job.status = JobStatus.TIMEOUT
        return job

    async def _cleanup_loop(self):
        """Periodically clean up stale jobs."""
        while self._running:
            now = time.time()
            stale_ids = [
                jid for jid, j in self.jobs.items()
                if j.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.TIMEOUT)
                and now - j.created_at > 3600  # Remove after 1 hour
            ]
            for jid in stale_ids:
                del self.jobs[jid]
            await asyncio.sleep(60)


# ---------------------------------------------------------------------------
# OpenAI-Compatible API Server
# ---------------------------------------------------------------------------

class OpenAIServer:
    """
    OpenAI-compatible REST API server.
    Routes inference requests to the distributed mining network.

    Usage:
        curl http://localhost:8080/v1/chat/completions \\
          -H "Content-Type: application/json" \\
          -d '{"model": "phi3-mini", "messages": [{"role": "user", "content": "Hello"}]}'

        curl http://localhost:8080/v1/embeddings \\
          -H "Content-Type: application/json" \\
          -d '{"model": "nomic-embed-text", "input": "Hello world"}'
    """

    def __init__(self, ollama_manager: OllamaManager, p2p_node=None):
        self.ollama = ollama_manager
        self.distributor = JobDistributor(ollama_manager, p2p_node)
        self.config = APIConfig()
        self._app: Optional[web.Application] = None
        self._runner: Optional[web.AppRunner] = None
        self._running = False

    def _create_app(self) -> web.Application:
        """Create the aiohttp web application."""
        app = web.Application()

        # Routes
        app.router.add_get("/v1/models", self._handle_list_models)
        app.router.add_post("/v1/chat/completions", self._handle_chat_completions)
        app.router.add_post("/v1/embeddings", self._handle_embeddings)
        app.router.add_get("/v1/health", self._handle_health)

        # Mining node internal routes
        app.router.add_post("/internal/jobs/claim", self._handle_job_claim)
        app.router.add_post("/internal/jobs/complete", self._handle_job_complete)
        app.router.add_post("/internal/jobs/fail", self._handle_job_fail)

        return app

    async def start(self, host: str = "0.0.0.0", port: int = 8080):
        """Start the API server."""
        self.config.host = host
        self.config.port = port

        self._app = self._create_app()
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()

        site = web.TCPSite(self._runner, host, port)
        await site.start()

        await self.distributor.start()
        self._running = True

        logger.info(f"OpenAI-compatible API server running on http://{host}:{port}")
        logger.info(f"  POST /v1/chat/completions - Chat completions")
        logger.info(f"  POST /v1/embeddings - Embeddings")
        logger.info(f"  GET  /v1/models - List models")
        logger.info(f"  GET  /v1/health - Health check")

    async def stop(self):
        """Stop the API server."""
        self._running = False
        await self.distributor.stop()
        if self._runner:
            await self._runner.cleanup()
        logger.info("API server stopped")

    # --- Authentication ---

    def _check_auth(self, request: web.Request) -> bool:
        """Check API key authentication."""
        if not self.config.require_api_key:
            return True
        api_key = request.headers.get("Authorization", "").replace("Bearer ", "")
        return api_key in self.config.api_keys

    # --- Handlers ---

    async def _handle_list_models(self, request: web.Request) -> web.Response:
        """GET /v1/models - List available models."""
        if not self._check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)

        models = []
        for name, model in MODEL_REGISTRY.items():
            models.append({
                "id": name,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "tokencoin",
                "permission": [],
                "root": name,
                "parent": None,
                "capabilities": {
                    "type": model.inference_type,
                    "parameters_b": model.parameters_billions,
                    "min_memory_gb": model.min_memory_gb,
                },
            })

        return web.json_response({
            "object": "list",
            "data": models,
        })

    async def _handle_chat_completions(self, request: web.Request) -> web.Response:
        """POST /v1/chat/completions - Chat completion."""
        if not self._check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)

        try:
            body = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        model_name = body.get("model", CONFIG.ollama.mining_model)
        messages = body.get("messages", [])
        max_tokens = body.get("max_tokens", CONFIG.ollama.max_tokens_per_job)
        temperature = body.get("temperature", CONFIG.ollama.inference_temperature)
        stream = body.get("stream", False)

        # Validate model — any Ollama model is accepted
        model = MODEL_REGISTRY.get(model_name)

        # Convert messages to a single prompt
        prompt = self._messages_to_prompt(messages)

        # Create a job
        job = APIJob(
            job_id=f"job_{uuid.uuid4().hex[:12]}",
            model=model_name,
            prompt=prompt,
            job_type="chat",
            max_tokens=max_tokens,
            temperature=temperature,
            seed=int(time.time()),
        )

        # Handle streaming
        if stream:
            return await self._handle_streaming_chat(request, job)

        # Submit to mining network
        job = await self.distributor.submit_job(job)

        # Wait for result (with timeout)
        job = await self.distributor.wait_for_result(
            job.job_id, timeout=self.config.job_completion_timeout_seconds
        )

        if job and job.status == JobStatus.COMPLETED and job.result:
            return web.json_response(job.result)
        elif job and job.error:
            return web.json_response({"error": job.error}, status=500)
        else:
            return web.json_response({
                "error": "No miner available to process the request",
                "hint": "Ensure at least one mining node is running on the network",
            }, status=503)

    async def _handle_streaming_chat(self, request: web.Request,
                                       job: APIJob) -> web.StreamResponse:
        """Handle streaming chat completions via SSE."""
        response = web.StreamResponse(
            status=200,
            reason="OK",
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )
        await response.prepare(request)

        # Submit job and poll for partial results
        job = await self.distributor.submit_job(job)

        # For streaming, we poll until complete and send the full result
        # In production, this would stream tokens as they're generated
        job = await self.distributor.wait_for_result(
            job.job_id, timeout=self.config.job_completion_timeout_seconds
        )

        if job and job.status == JobStatus.COMPLETED and job.result:
            content = job.result.get("choices", [{}])[0].get("message", {}).get("content", "")
            # Send as SSE chunks
            for i, char in enumerate(content):
                chunk = {
                    "id": f"chatcmpl-{job.job_id}",
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": job.model,
                    "choices": [{
                        "index": 0,
                        "delta": {"content": char},
                        "finish_reason": None,
                    }],
                }
                await response.write(f"data: {json.dumps(chunk)}\n\n".encode())
                await asyncio.sleep(0.01)  # Simulate streaming

            # Send finish
            finish = {
                "id": f"chatcmpl-{job.job_id}",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": job.model,
                "choices": [{
                    "index": 0,
                    "delta": {},
                    "finish_reason": "stop",
                }],
            }
            await response.write(f"data: {json.dumps(finish)}\n\n".encode())
        else:
            await response.write(f"data: {json.dumps({'error': 'Mining failed'})}\n\n".encode())

        await response.write("data: [DONE]\n\n".encode())
        return response

    async def _handle_embeddings(self, request: web.Request) -> web.Response:
        """POST /v1/embeddings - Generate embeddings."""
        if not self._check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)

        try:
            body = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        model_name = body.get("model", "nomic-embed-text")
        input_text = body.get("input", "")

        if isinstance(input_text, list):
            input_text = " ".join(input_text)

        # Any Ollama embedding model is accepted
        model = MODEL_REGISTRY.get(model_name)

        # Create embedding job
        job = APIJob(
            job_id=f"emb_{uuid.uuid4().hex[:12]}",
            model=model_name,
            prompt=input_text,
            job_type="embedding",
        )

        job = await self.distributor.submit_job(job)
        job = await self.distributor.wait_for_result(
            job.job_id, timeout=self.config.job_completion_timeout_seconds
        )

        if job and job.status == JobStatus.COMPLETED and job.result:
            return web.json_response(job.result)
        elif job and job.error:
            return web.json_response({"error": job.error}, status=500)
        else:
            return web.json_response({
                "error": "No miner available to process the request",
            }, status=503)

    async def _handle_health(self, request: web.Request) -> web.Response:
        """GET /v1/health - Health check."""
        hw = self.ollama.hardware
        return web.json_response({
            "status": "ok",
            "version": "0.1.0",
            "network": "tokencoin",
            "hardware": {
                "backend": hw.backend.value,
                "cpu_threads": hw.cpu_threads,
                "ram_gb": round(hw.ram_total_gb, 1),
                "gpu": hw.gpu_name if hw.has_gpu else None,
                "vram_gb": hw.vram_total_gb if hw.has_gpu else None,
            },
            "models_available": list(MODEL_REGISTRY.keys()),
            "jobs_pending": sum(
                1 for j in self.distributor.jobs.values()
                if j.status == JobStatus.PENDING
            ),
            "uptime_seconds": time.time() - self._start_time if hasattr(self, '_start_time') else 0,
        })

    # --- Internal endpoints for mining nodes ---

    async def _handle_job_claim(self, request: web.Request) -> web.Response:
        """POST /internal/jobs/claim - Miner claims a job."""
        try:
            body = await request.json()
            job_id = body.get("job_id", "")
            miner_id = body.get("miner_id", "")
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        job = self.distributor.claim_job(job_id, miner_id)
        if job:
            return web.json_response({"status": "claimed", "job": job.to_dict()})
        return web.json_response({"status": "not_found"}, status=404)

    async def _handle_job_complete(self, request: web.Request) -> web.Response:
        """POST /internal/jobs/complete - Miner completes a job."""
        try:
            body = await request.json()
            job_id = body.get("job_id", "")
            result = body.get("result", {})
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        if self.distributor.complete_job(job_id, result):
            return web.json_response({"status": "completed"})
        return web.json_response({"status": "not_found"}, status=404)

    async def _handle_job_fail(self, request: web.Request) -> web.Response:
        """POST /internal/jobs/fail - Miner reports failure."""
        try:
            body = await request.json()
            job_id = body.get("job_id", "")
            error = body.get("error", "")
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        self.distributor.fail_job(job_id, error)
        return web.json_response({"status": "recorded"})

    # --- Helpers ---

    @staticmethod
    def _messages_to_prompt(messages: List[Dict[str, str]]) -> str:
        """Convert OpenAI-style messages to a single prompt string."""
        prompt_parts = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                prompt_parts.append(f"System: {content}")
            elif role == "user":
                prompt_parts.append(f"User: {content}")
            elif role == "assistant":
                prompt_parts.append(f"Assistant: {content}")
        prompt_parts.append("Assistant: ")
        return "\n".join(prompt_parts)
