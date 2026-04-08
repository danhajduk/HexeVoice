from dataclasses import dataclass


@dataclass(frozen=True)
class OnboardingStep:
    step_id: str
    label: str
    lifecycle_state: str
    phase: str


CANONICAL_ONBOARDING_STEPS: tuple[OnboardingStep, ...] = (
    OnboardingStep(
        step_id="node_identity",
        label="Node Identity",
        lifecycle_state="unconfigured",
        phase="phase1",
    ),
    OnboardingStep(
        step_id="core_connection",
        label="Core Connection",
        lifecycle_state="bootstrap_connecting",
        phase="phase1",
    ),
    OnboardingStep(
        step_id="bootstrap_discovery",
        label="Bootstrap Discovery",
        lifecycle_state="core_discovered",
        phase="phase1",
    ),
    OnboardingStep(
        step_id="registration",
        label="Registration",
        lifecycle_state="registration_pending",
        phase="phase1",
    ),
    OnboardingStep(
        step_id="approval",
        label="Approval",
        lifecycle_state="pending_approval",
        phase="phase1",
    ),
    OnboardingStep(
        step_id="trust_activation",
        label="Trust Activation",
        lifecycle_state="trusted",
        phase="phase1",
    ),
    OnboardingStep(
        step_id="provider_setup",
        label="Provider Setup",
        lifecycle_state="capability_setup_pending",
        phase="phase2",
    ),
    OnboardingStep(
        step_id="capability_declaration",
        label="Capability Declaration",
        lifecycle_state="capability_declaration_in_progress",
        phase="phase2",
    ),
    OnboardingStep(
        step_id="governance_sync",
        label="Governance Sync",
        lifecycle_state="capability_declaration_accepted",
        phase="phase2",
    ),
    OnboardingStep(
        step_id="ready",
        label="Ready",
        lifecycle_state="operational",
        phase="phase2",
    ),
)


def initial_onboarding_step() -> OnboardingStep:
    return CANONICAL_ONBOARDING_STEPS[0]
