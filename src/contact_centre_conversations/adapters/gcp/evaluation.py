"""Managed EvaluationGatePort: the model-quality-gate promotion authority over HTTP.

model-quality-gate owns the promotion verdict for the whole catalog (it is the E1/E2 gate owner), so
this adapter asks rather than decides. It carries no thresholds of its own: a repo that scored
itself and promoted itself would be a gate in name only.

The client comes from ``agent-eval-kit`` so the wire contract is shared with every other repo, and
it is constructed lazily because building a container must not require the quality service to be
reachable.
"""

from __future__ import annotations

from agent_eval_kit import EvalReport, PromotionGateClient
from hex_service_kit.netdefaults import ConfiguredEmptyError, read_env_setting

from ...config import Settings
from ...domain.modes import ModeConfigurationError

# There is deliberately no bundle constant in this file. Each mode's promotion bundle is
# already resolved into its own ``ModeGate.promotion_bundle`` from the settings block, and that
# is the one home for it: a constant here would be a second, and the two would diverge the first
# time a deployment renamed a bundle.
#
# What used to be here was `_BUNDLE = "contact-centre-conversations"`, the bare repository name,
# which the authority does not register at all: it registers the two mode-scoped bundles and no
# blended third, because one bundle would let a strong agent-assist result carry a weak
# customer-facing one over the line. Every promotion request from this adapter would therefore
# have failed closed on `UnknownMetricError`. Nothing reported it, because the offline smoke
# gate never touches this path.
_QUALITY_URL_ENV = "CONTACT_QUALITY_URL"
_DEFAULT_QUALITY_URL = "http://localhost:8084"
#: The model the verdict is recorded AGAINST. model-quality-gate keys a promotion to the exact model
#: and
#: prompt version that produced the evidence, so a model swap invalidates the old verdict
#: rather than inheriting it. Change this in the same commit that changes the model.
_GATED_MODEL = "gemini-3.5-flash"


class ManagedEvalGateAdapter:
    """Delegates evaluation and promotion to the model-quality-gate AI-quality service."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: PromotionGateClient | None = None

    def _bundle(self) -> str:
        """The registered bundle for the ONE mode this deployment serves, or refuse.

        A deployment that enables both modes has TWO promotions, not one, and this port has a
        single ``evaluate(dataset_path)`` with no mode in the signature. Guessing would mean
        letting a strong agent-assist result carry a weak customer-facing one over the line,
        which is the exact thing gating the modes apart exists to prevent. So it refuses, by
        name, and points at the runner that already asks per rubric.
        """
        modes = self._settings.modes
        enabled = modes.enabled_modes
        if not enabled:
            raise ModeConfigurationError(
                "no contact mode is enabled, so there is no promotion to ask about; a verdict "
                "over no enabled mode would certify nothing"
            )
        if len(enabled) > 1:
            names = ", ".join(modes.gate(mode).promotion_bundle or mode.value for mode in enabled)
            raise ModeConfigurationError(
                "both contact modes are enabled, so this deployment has TWO promotions and this "
                f"port carries one bundle. Ask per rubric with `python eval/run_eval.py --mode "
                f"gate`, which sends {names} separately. A single blended verdict would let a "
                "strong agent-assist result carry a weak customer-facing one."
            )
        bundle = modes.gate(enabled[0]).promotion_bundle
        if not bundle:
            raise ModeConfigurationError(
                f"mode {enabled[0].value!r} is enabled with no promotion_bundle, so there is no "
                "registered metric set to ask the authority for. Name the bundle whose rubric "
                "set authorised this mode."
            )
        return bundle

    def _gate_client(self) -> PromotionGateClient:
        if self._client is None:
            # Three states, because this names WHERE the promotion authority is. Unset takes the
            # documented default. EMPTIED names no authority at all, and a gate with no authority
            # must refuse rather than quietly fall back to a default the operator just removed.
            setting = read_env_setting(_QUALITY_URL_ENV)
            if setting.is_configured_empty:
                raise ConfiguredEmptyError(
                    f"{_QUALITY_URL_ENV} is set but empty, so no promotion authority is named. "
                    f"Unset it to use {_DEFAULT_QUALITY_URL}, or give it the model-quality-gate "
                    f"service URL."
                )
            url = setting.value or _DEFAULT_QUALITY_URL
            self._client = PromotionGateClient(url, bundle=self._bundle(), model=_GATED_MODEL)
        return self._client

    def evaluate(self, dataset_path: str) -> EvalReport:
        return self._gate_client().evaluate(dataset_path)

    def gate(self, target: str) -> bool:
        return self._gate_client().gate(target)
