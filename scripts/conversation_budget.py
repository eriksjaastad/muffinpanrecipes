"""Tool-only Anthropic budget guard for conversation-lab experiments.

The guard supports only the plain-text Messages API shape used by the lab.
It reserves a conservative input estimate and the full requested output limit
before each generation request, then settles against validated response usage.
Its allowance is an operational bound based on current public prices and
known request shapes, not a provider-certified billing guarantee.
"""

from __future__ import annotations

import contextvars
import fcntl
import json
import math
import os
import uuid
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import patch


MICRO_USD_PER_USD = 1_000_000
MAX_BUDGET_MICRO_USD = 5 * MICRO_USD_PER_USD
SCHEMA_VERSION = 1
PHASES = ("calibration", "bench", "ab")

# Values are integer microdollars per token: price per million tokens.
MODEL_RATES = {
    "claude-haiku-4-5-20251001": (1, 5),
    "claude-opus-4-6": (5, 25),
}


class BudgetGuardError(RuntimeError):
    """The guarded experiment cannot safely make its next request."""


class BudgetLedgerError(BudgetGuardError):
    """The persisted ledger is absent, corrupt, inconsistent, or unwritable."""


class BudgetExceeded(BudgetGuardError):
    """A new request would exceed the combined approved budget."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _usd_to_micro(value: Decimal | int | str) -> int:
    try:
        amount = Decimal(str(value))
    except Exception as exc:
        raise ValueError("budget must be a finite USD amount") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError("budget must be a finite non-negative USD amount")
    micro = amount * MICRO_USD_PER_USD
    if micro != micro.to_integral_value():
        raise ValueError("budget precision cannot exceed one microdollar")
    result = int(micro)
    if result > MAX_BUDGET_MICRO_USD:
        raise ValueError("budget exceeds the approved $5.00 combined ceiling")
    return result


def _format_budget_usd(budget_microusd: int) -> str:
    """Format the configured ceiling exactly to its supported microdollar precision."""
    amount = f"${Decimal(budget_microusd) / MICRO_USD_PER_USD:,.6f}"
    return amount.rstrip("0").rstrip(".")


def _new_totals() -> dict[str, int]:
    return {
        "count_token_requests": 0,
        "count_token_failures": 0,
        "generation_attempts": 0,
        "actual_microusd": 0,
        "reserved_microusd": 0,
        "uncertain_microusd": 0,
    }


def _new_ledger(budget_microusd: int) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": _now(),
        "budget_microusd": budget_microusd,
        "status": "active",
        "stop_reason": None,
        "totals": _new_totals(),
        "phases": {phase: _new_totals() for phase in PHASES},
        "calls": [],
    }


def _validate_ledger(ledger: Any, expected_budget: int) -> dict[str, Any]:
    if not isinstance(ledger, dict):
        raise BudgetLedgerError("budget ledger is not an object")
    expected_keys = {
        "schema_version", "created_at", "budget_microusd", "status",
        "stop_reason", "totals", "phases", "calls",
    }
    if set(ledger) != expected_keys:
        raise BudgetLedgerError("budget ledger fields are malformed")
    if ledger.get("schema_version") != SCHEMA_VERSION:
        raise BudgetLedgerError("unsupported budget ledger version")
    if ledger.get("budget_microusd") != expected_budget:
        raise BudgetLedgerError("budget ledger amount does not match this run")
    if not isinstance(ledger.get("created_at"), str):
        raise BudgetLedgerError("budget ledger creation time is malformed")
    if ledger.get("status") not in {"active", "stopped"}:
        raise BudgetLedgerError("budget ledger has an invalid status")
    if ledger.get("status") == "stopped" and not isinstance(ledger.get("stop_reason"), str):
        raise BudgetLedgerError("stopped budget ledger has no reason")
    if ledger.get("status") == "active" and ledger.get("stop_reason") is not None:
        raise BudgetLedgerError("active budget ledger has a stop reason")
    if not isinstance(ledger.get("totals"), dict) or not isinstance(ledger.get("phases"), dict):
        raise BudgetLedgerError("budget ledger totals are malformed")
    if set(ledger["phases"]) != set(PHASES):
        raise BudgetLedgerError("budget ledger phases are malformed")
    if not isinstance(ledger.get("calls"), list):
        raise BudgetLedgerError("budget ledger call history is malformed")
    for totals in [ledger["totals"], *ledger["phases"].values()]:
        if not isinstance(totals, dict):
            raise BudgetLedgerError("budget ledger totals are malformed")
        if set(totals) != set(_new_totals()):
            raise BudgetLedgerError("budget ledger totals fields are malformed")
        for key in _new_totals():
            value = totals.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise BudgetLedgerError(f"budget ledger field {key} is invalid")
    for key in _new_totals():
        if sum(ledger["phases"][phase][key] for phase in PHASES) != ledger["totals"][key]:
            raise BudgetLedgerError(f"budget ledger phase totals do not reconcile for {key}")
    call_actual = call_reserved = call_uncertain = 0
    seen_call_ids: set[str] = set()
    reserved_calls = 0
    for call in ledger["calls"]:
        if not isinstance(call, dict) or call.get("phase") not in PHASES:
            raise BudgetLedgerError("budget ledger call entry is malformed")
        if set(call) != {
            "id", "phase", "model", "status", "reservation_microusd",
            "actual_microusd", "uncertain_microusd",
        }:
            raise BudgetLedgerError("budget ledger call fields are malformed")
        if call.get("model") not in MODEL_RATES or not isinstance(call.get("id"), str):
            raise BudgetLedgerError("budget ledger call identity is malformed")
        if call["id"] in seen_call_ids:
            raise BudgetLedgerError("budget ledger call ids are duplicated")
        seen_call_ids.add(call["id"])
        status = call.get("status")
        if status not in {"reserved", "settled", "uncertain", "over_reservation"}:
            raise BudgetLedgerError("budget ledger call status is malformed")
        for key in ("reservation_microusd", "actual_microusd", "uncertain_microusd"):
            value = call.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise BudgetLedgerError("budget ledger call amount is malformed")
        reservation = call["reservation_microusd"]
        actual = call["actual_microusd"]
        uncertain = call["uncertain_microusd"]
        if status == "reserved":
            if actual or uncertain:
                raise BudgetLedgerError("unsettled call has settled amounts")
            reserved_calls += 1
            call_reserved += reservation
        elif status == "settled":
            if uncertain or actual > reservation:
                raise BudgetLedgerError("settled call amounts are inconsistent")
        elif status == "uncertain":
            if actual or uncertain != reservation:
                raise BudgetLedgerError("uncertain call amounts are inconsistent")
            call_uncertain += uncertain
        elif status == "over_reservation":
            if uncertain or actual <= reservation:
                raise BudgetLedgerError("over-reservation call amounts are inconsistent")
        call_actual += actual
    if len(ledger["calls"]) != ledger["totals"]["generation_attempts"]:
        raise BudgetLedgerError("budget ledger generation count does not match call history")
    if reserved_calls > 1:
        raise BudgetLedgerError("budget ledger has concurrent outstanding requests")
    if call_actual != ledger["totals"]["actual_microusd"]:
        raise BudgetLedgerError("budget ledger actual charges do not match call history")
    if call_reserved != ledger["totals"]["reserved_microusd"]:
        raise BudgetLedgerError("budget ledger reservations do not match call history")
    if call_uncertain != ledger["totals"]["uncertain_microusd"]:
        raise BudgetLedgerError("budget ledger uncertain charges do not match call history")
    accounted = sum(ledger["totals"][key] for key in (
        "actual_microusd", "reserved_microusd", "uncertain_microusd",
    ))
    if accounted > expected_budget and not (
        ledger["status"] == "stopped"
        and ledger["stop_reason"] == "usage_exceeded_reservation"
    ):
        raise BudgetLedgerError("budget ledger accounting exceeds its budget")
    return ledger


class AnthropicBudgetGuard:
    """Patch the Anthropic SDK request seam and account one shared ledger."""

    def __init__(
        self,
        ledger_path: str | Path,
        *,
        budget_usd: Decimal | int | str = 5,
        create: bool = False,
    ) -> None:
        self.path = Path(ledger_path)
        self.budget_microusd = _usd_to_micro(budget_usd)
        self.create = create
        self._patches: ExitStack | None = None
        self._phase: contextvars.ContextVar[str] = contextvars.ContextVar(
            f"conversation_budget_phase_{id(self)}", default="calibration"
        )
        self._local_stop: str | None = None

    def __enter__(self) -> AnthropicBudgetGuard:
        self._initialize_or_resume()
        self._install_patches()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._patches is not None:
            self._patches.close()
            self._patches = None

    @contextmanager
    def phase(self, name: str) -> Iterator[None]:
        if name not in PHASES:
            raise ValueError(f"phase must be one of {', '.join(PHASES)}")
        token = self._phase.set(name)
        try:
            yield
        finally:
            self._phase.reset(token)

    def validate_configured_models(
        self,
        *,
        dialogue_model: str | None,
        judge_model: str | None,
    ) -> None:
        """Fail closed before dispatch if configured routes exceed guard scope.

        The SDK hooks cannot observe failures raised earlier by model_router's
        provider and role allowlists. Validate the exact configured roles here
        so production's fail-closed judge handler cannot turn a bad model into
        a scored FAIL while later paid arms continue.
        """
        from backend.utils import model_router

        configured = []
        if dialogue_model is not None:
            configured.append(("dialogue", dialogue_model))
        configured.append(("judge", judge_model))
        for role, raw_model in configured:
            try:
                routed = model_router.parse_model(raw_model)
            except Exception as exc:
                self._reject(f"invalid_{role}_model")
                raise AssertionError("unreachable") from exc

            if routed.provider != "anthropic":
                self._reject(f"unsupported_{role}_provider")
            if routed.model not in MODEL_RATES:
                self._reject(f"unsupported_{role}_model")
            if role == "dialogue":
                try:
                    model_router.ensure_anthropic_model_allowed(routed.model)
                except Exception as exc:
                    self._reject("dialogue_model_not_allowlisted")
                    raise AssertionError("unreachable") from exc
            elif routed.model not in model_router.JUDGE_ALLOWLIST:
                self._reject("judge_model_not_allowlisted")

    def _lock(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = open(self.path.with_name(self.path.name + ".lock"), "a+b")
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        return lock_file

    def _read_unlocked(self) -> dict[str, Any]:
        try:
            raw = self.path.read_text(encoding="utf-8")
            ledger = json.loads(raw)
        except FileNotFoundError as exc:
            raise BudgetLedgerError("budget ledger is missing; refusing to create it on resume") from exc
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise BudgetLedgerError("budget ledger cannot be read; refusing to reset it") from exc
        return _validate_ledger(ledger, self.budget_microusd)

    def _write_unlocked(self, ledger: dict[str, Any]) -> None:
        _validate_ledger(ledger, self.budget_microusd)
        temp_path = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with open(temp_path, "x", encoding="utf-8") as handle:
                json.dump(ledger, handle, sort_keys=True, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.path)
            dir_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except Exception as exc:
            self._local_stop = "ledger_write_failure"
            raise BudgetLedgerError("budget ledger write failed; stopping guarded requests") from exc

    def _initialize_or_resume(self) -> None:
        lock_file = self._lock()
        try:
            if self.create:
                if self.path.exists():
                    raise BudgetLedgerError("refusing to replace an existing budget ledger")
                self._write_unlocked(_new_ledger(self.budget_microusd))
            else:
                ledger = self._read_unlocked()
                if ledger["status"] == "stopped":
                    raise BudgetGuardError(f"budget ledger is stopped: {ledger['stop_reason']}")
                outstanding = ledger["totals"]["reserved_microusd"]
                if outstanding:
                    ledger["totals"]["reserved_microusd"] = 0
                    ledger["totals"]["uncertain_microusd"] += outstanding
                    for call in ledger["calls"]:
                        if call["status"] == "reserved":
                            call["status"] = "uncertain"
                            call["uncertain_microusd"] = call["reservation_microusd"]
                    for phase in PHASES:
                        phase_reserved = ledger["phases"][phase]["reserved_microusd"]
                        if phase_reserved:
                            ledger["phases"][phase]["reserved_microusd"] = 0
                            ledger["phases"][phase]["uncertain_microusd"] += phase_reserved
                    ledger["status"] = "stopped"
                    ledger["stop_reason"] = "interrupted_request_uncertain"
                    self._write_unlocked(ledger)
                    raise BudgetGuardError("previous request reservation is uncertain; refusing to resume")
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()

    def _transaction(self, update):
        lock_file = self._lock()
        try:
            ledger = self._read_unlocked()
            if ledger["status"] != "active":
                raise BudgetGuardError(f"budget ledger is stopped: {ledger['stop_reason']}")
            try:
                result = update(ledger)
            except Exception:
                # Some denials deliberately latch a stopped status before
                # raising. Persist that state so downstream exception handling
                # cannot accidentally turn the experiment back into active work.
                if ledger != self._read_unlocked():
                    self._write_unlocked(ledger)
                raise
            self._write_unlocked(ledger)
            return result
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()

    def _stop(self, reason: str) -> None:
        self._local_stop = reason

        def update(ledger):
            ledger["status"] = "stopped"
            ledger["stop_reason"] = reason

        self._transaction(update)

    def raise_if_stopped(self) -> None:
        if self._local_stop:
            raise BudgetGuardError(f"budget guard stopped: {self._local_stop}")
        lock_file = self._lock()
        try:
            ledger = self._read_unlocked()
            if ledger["status"] != "active":
                raise BudgetGuardError(f"budget ledger is stopped: {ledger['stop_reason']}")
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()

    def summary(self) -> dict[str, Any]:
        lock_file = self._lock()
        try:
            ledger = self._read_unlocked()
            return {
                "budget_microusd": ledger["budget_microusd"],
                "status": ledger["status"],
                "stop_reason": ledger["stop_reason"],
                "totals": dict(ledger["totals"]),
                "phases": {key: dict(value) for key, value in ledger["phases"].items()},
            }
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()

    def _record_count_attempt(self, phase: str) -> None:
        def update(ledger):
            ledger["totals"]["count_token_requests"] += 1
            ledger["phases"][phase]["count_token_requests"] += 1
        self._transaction(update)

    def _record_count_failure(self, phase: str) -> None:
        def update(ledger):
            ledger["totals"]["count_token_failures"] += 1
            ledger["phases"][phase]["count_token_failures"] += 1
            ledger["status"] = "stopped"
            ledger["stop_reason"] = "count_tokens_failed"
        self._local_stop = "count_tokens_failed"
        self._transaction(update)

    def _reserve(self, phase: str, model: str, reserve: int) -> str:
        call_id = uuid.uuid4().hex

        def update(ledger):
            totals = ledger["totals"]
            if totals["reserved_microusd"]:
                ledger["status"] = "stopped"
                ledger["stop_reason"] = "concurrent_or_unsettled_request"
                self._local_stop = "concurrent_or_unsettled_request"
                raise BudgetGuardError("another generation reservation is unsettled")
            accounted = sum(totals[key] for key in (
                "actual_microusd", "reserved_microusd", "uncertain_microusd",
            ))
            if accounted + reserve > self.budget_microusd:
                ledger["status"] = "stopped"
                ledger["stop_reason"] = "budget_exhausted"
                self._local_stop = "budget_exhausted"
                raise BudgetExceeded(
                    "request denied before generation: combined budget reservation "
                    f"exceeds configured ceiling {_format_budget_usd(self.budget_microusd)}"
                )
            totals["generation_attempts"] += 1
            totals["reserved_microusd"] += reserve
            phase_totals = ledger["phases"][phase]
            phase_totals["generation_attempts"] += 1
            phase_totals["reserved_microusd"] += reserve
            ledger["calls"].append({
                "id": call_id,
                "phase": phase,
                "model": model,
                "status": "reserved",
                "reservation_microusd": reserve,
                "actual_microusd": 0,
                "uncertain_microusd": 0,
            })
            return call_id
        return self._transaction(update)

    def _finish_call(self, call_id: str, phase: str, actual: int | None, failure: str | None = None) -> None:
        def update(ledger):
            call = next((item for item in ledger["calls"] if item["id"] == call_id), None)
            if call is None or call["status"] != "reserved":
                ledger["status"] = "stopped"
                ledger["stop_reason"] = "accounting_invariant_failure"
                self._local_stop = "accounting_invariant_failure"
                raise BudgetLedgerError("generation reservation is missing or already settled")
            reserved = call["reservation_microusd"]
            ledger["totals"]["reserved_microusd"] -= reserved
            ledger["phases"][phase]["reserved_microusd"] -= reserved
            if failure:
                ledger["totals"]["uncertain_microusd"] += reserved
                ledger["phases"][phase]["uncertain_microusd"] += reserved
                call["status"] = "uncertain"
                call["uncertain_microusd"] = reserved
                ledger["status"] = "stopped"
                ledger["stop_reason"] = failure
                self._local_stop = failure
                return
            assert actual is not None
            call["actual_microusd"] = actual
            ledger["totals"]["actual_microusd"] += actual
            ledger["phases"][phase]["actual_microusd"] += actual
            if actual > reserved:
                call["status"] = "over_reservation"
                ledger["status"] = "stopped"
                ledger["stop_reason"] = "usage_exceeded_reservation"
                self._local_stop = "usage_exceeded_reservation"
            else:
                call["status"] = "settled"
        self._transaction(update)

    def _install_patches(self) -> None:
        import anthropic
        from anthropic.resources.messages import Messages
        from backend.utils import model_router

        original_client = anthropic.Anthropic
        original_create = Messages.create
        original_count = Messages.count_tokens
        stack = ExitStack()

        def client_no_retries(*args, **kwargs):
            kwargs["max_retries"] = 0
            return original_client(*args, **kwargs)

        def reject_provider(*args, **kwargs):
            self._reject("unsupported_provider")

        def count_tokens(client, *args, **kwargs):
            phase = self._phase.get()
            self._validate_count_kwargs(args, kwargs)
            model = kwargs["model"]
            self._check_model(model)
            self._record_count_attempt(phase)
            try:
                response = original_count(client, **kwargs)
            except Exception:
                self._record_count_failure(phase)
                raise
            tokens = getattr(response, "input_tokens", None)
            if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 0:
                self._record_count_failure(phase)
                raise BudgetGuardError("count_tokens returned invalid usage; stopping before generation")
            return response

        def create(client, *args, **kwargs):
            phase = self._phase.get()
            self._validate_create_kwargs(args, kwargs)
            model = kwargs["model"]
            self._check_model(model)
            count_kwargs = {key: kwargs[key] for key in ("model", "messages")}
            if "system" in kwargs:
                count_kwargs["system"] = kwargs["system"]
            count_response = client.count_tokens(**count_kwargs)
            count_tokens_estimate = count_response.input_tokens
            text_bytes = len(kwargs["messages"][0]["content"].encode("utf-8"))
            if "system" in kwargs:
                text_bytes += len(kwargs["system"].encode("utf-8"))
            conservative_input = max(
                text_bytes + 4096,
                math.ceil(count_tokens_estimate * 1.25) + 1024,
            )
            input_rate, output_rate = MODEL_RATES[model]
            reserve = conservative_input * input_rate + kwargs["max_tokens"] * output_rate
            call_id = self._reserve(phase, model, reserve)
            # Pin pricing modifiers to the public rates represented in ledger.
            # Haiku 4.5 predates the inference_geo request parameter.
            kwargs["service_tier"] = "standard_only"
            if model == "claude-opus-4-6":
                kwargs["inference_geo"] = "global"
            try:
                response = original_create(client, *args, **kwargs)
            except Exception:
                self._finish_call(call_id, phase, None, "generation_request_uncertain")
                raise
            try:
                input_tokens, output_tokens = self._validated_usage(response, model)
                actual = input_tokens * input_rate + output_tokens * output_rate
            except Exception as exc:
                self._finish_call(call_id, phase, None, "generation_usage_uncertain")
                if isinstance(exc, BudgetGuardError):
                    raise
                raise BudgetGuardError("generation response usage has an unknown shape") from exc
            self._finish_call(call_id, phase, actual)
            self.raise_if_stopped()
            return response

        try:
            stack.enter_context(patch.object(anthropic, "Anthropic", client_no_retries))
            stack.enter_context(patch.object(Messages, "count_tokens", count_tokens))
            stack.enter_context(patch.object(Messages, "create", create))
            for name in (
                "_generate_openai",
                "_generate_google",
                "_generate_vision_openai",
                "_generate_vision_google",
                "_generate_vision_anthropic",
            ):
                if hasattr(model_router, name):
                    stack.enter_context(patch.object(model_router, name, reject_provider))
        except Exception:
            stack.close()
            raise
        self._patches = stack

    def _reject(self, reason: str) -> None:
        self._stop(reason)
        raise BudgetGuardError(f"request rejected by conversation budget guard: {reason}")

    def _check_model(self, model: Any) -> None:
        if not isinstance(model, str) or model not in MODEL_RATES:
            self._reject("unsupported_model")

    def _validate_count_kwargs(self, args: tuple, kwargs: dict) -> None:
        if args or set(kwargs) - {"model", "messages", "system"}:
            self._reject("unsupported_count_tokens_request")
        if not {"model", "messages"} <= set(kwargs):
            self._reject("unsupported_count_tokens_request")
        if not self._valid_messages(kwargs["messages"]):
            self._reject("unsupported_count_tokens_request")
        if "system" in kwargs and not isinstance(kwargs["system"], str):
            self._reject("unsupported_count_tokens_request")

    def _validate_create_kwargs(self, args: tuple, kwargs: dict) -> None:
        allowed = {
            "model", "max_tokens", "temperature", "messages", "system",
            "service_tier", "inference_geo",
        }
        if args or set(kwargs) - allowed:
            self._reject("unsupported_generation_request_shape")
        if not {"model", "max_tokens", "temperature", "messages"} <= set(kwargs):
            self._reject("unsupported_generation_request_shape")
        model = kwargs["model"]
        if "service_tier" in kwargs and kwargs["service_tier"] != "standard_only":
            self._reject("unsupported_generation_billing_modifier")
        if "inference_geo" in kwargs and not (
            model == "claude-opus-4-6" and kwargs["inference_geo"] == "global"
        ):
            self._reject("unsupported_generation_billing_modifier")
        if not self._valid_messages(kwargs["messages"]):
            self._reject("unsupported_generation_request_shape")
        if "system" in kwargs and not isinstance(kwargs["system"], str):
            self._reject("unsupported_generation_request_shape")
        max_tokens = kwargs["max_tokens"]
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 1 <= max_tokens <= 4096:
            self._reject("unsupported_generation_request_shape")
        temperature = kwargs["temperature"]
        if isinstance(temperature, bool) or not isinstance(temperature, (float, int)) or not math.isfinite(temperature):
            self._reject("unsupported_generation_request_shape")

    @staticmethod
    def _valid_messages(messages: Any) -> bool:
        return (
            isinstance(messages, list)
            and len(messages) == 1
            and isinstance(messages[0], dict)
            and set(messages[0]) == {"role", "content"}
            and messages[0]["role"] == "user"
            and isinstance(messages[0]["content"], str)
        )

    @staticmethod
    def _validated_usage(response: Any, model: str) -> tuple[int, int]:
        usage = getattr(response, "usage", None)
        if usage is None:
            raise BudgetGuardError("generation response has no usage")
        if hasattr(usage, "model_dump"):
            fields = usage.model_dump(exclude_none=True)
        elif isinstance(usage, dict):
            fields = usage
        else:
            fields = vars(usage)
        if not isinstance(fields, dict):
            raise BudgetGuardError("generation response usage has an unknown shape")
        service_tier = fields.get("service_tier")
        if service_tier != "standard":
            raise BudgetGuardError("generation response used an unpriced service tier")
        inference_geo = fields.get("inference_geo")
        if model == "claude-opus-4-6" and inference_geo != "global":
            raise BudgetGuardError("Opus response has unknown or nonstandard inference geography")
        if model == "claude-haiku-4-5-20251001" and inference_geo not in (None, "not_available", "global"):
            raise BudgetGuardError("Haiku response has unknown inference geography")
        for key, value in fields.items():
            if key in {"input_tokens", "output_tokens", "service_tier", "inference_geo"}:
                continue
            if _contains_nonzero(value):
                raise BudgetGuardError("generation response contains unpriced usage fields")
        input_tokens = fields.get("input_tokens")
        output_tokens = fields.get("output_tokens")
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0
               for value in (input_tokens, output_tokens)):
            raise BudgetGuardError("generation response usage is invalid")
        return input_tokens, output_tokens


def _contains_nonzero(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, dict):
        return any(_contains_nonzero(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_contains_nonzero(item) for item in value)
    return bool(value)
