from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
import random
from typing import Literal


ACTIVE_SPEAKER_PHRASE_SET_VERSION = "speaker-id-phrase-set-v1"


@dataclass(frozen=True)
class SpeakerPhrase:
    phrase_id: str
    text: str
    category: Literal["enrollment", "holdout_validation"]

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


ENROLLMENT_PHRASES: tuple[SpeakerPhrase, ...] = (
    SpeakerPhrase("enroll-001", "Hexe, turn on the lights in the living room.", "enrollment"),
    SpeakerPhrase("enroll-002", "What's the weather going to be like tomorrow morning?", "enrollment"),
    SpeakerPhrase("enroll-003", "Play some music in the kitchen and set the volume to forty percent.", "enrollment"),
    SpeakerPhrase("enroll-004", "Remind me to call the dentist when I get home.", "enrollment"),
    SpeakerPhrase("enroll-005", "Who is at the front door, and when did they arrive?", "enrollment"),
    SpeakerPhrase("enroll-006", "The quick brown fox jumps over the lazy dog.", "enrollment"),
    SpeakerPhrase("enroll-007", "Seven people bought fresh coffee, bread, cheese, and apples.", "enrollment"),
    SpeakerPhrase("enroll-008", "I'd like to know what's on my calendar for Friday afternoon.", "enrollment"),
    SpeakerPhrase("enroll-009", "Please turn the bedroom temperature down by two degrees.", "enrollment"),
    SpeakerPhrase("enroll-010", "Sometimes I speak quietly, and sometimes I speak much louder.", "enrollment"),
    SpeakerPhrase("enroll-011", "Hexe, what time is it?", "enrollment"),
    SpeakerPhrase("enroll-012", "Could you please tell me whether the garage door is still open?", "enrollment"),
    SpeakerPhrase("enroll-013", "Add tomatoes, pasta, olive oil, and basil to my shopping list.", "enrollment"),
    SpeakerPhrase("enroll-014", "Turn off the downstairs lights after the movie is finished.", "enrollment"),
    SpeakerPhrase("enroll-015", "Tell me how long the drive to the airport will take.", "enrollment"),
    SpeakerPhrase("enroll-016", "Please remind Sarah that the package is beside the front steps.", "enrollment"),
    SpeakerPhrase("enroll-017", "Set the hallway lights to a soft blue color tonight.", "enrollment"),
    SpeakerPhrase("enroll-018", "I need a quiet alarm for six fifteen tomorrow morning.", "enrollment"),
    SpeakerPhrase("enroll-019", "The old wooden clock stopped ticking during the storm.", "enrollment"),
    SpeakerPhrase("enroll-020", "Check whether any windows are open before bedtime.", "enrollment"),
    SpeakerPhrase("enroll-021", "Move my workout reminder from Monday to Wednesday evening.", "enrollment"),
    SpeakerPhrase("enroll-022", "Start a twenty five minute focus timer in the office.", "enrollment"),
    SpeakerPhrase("enroll-023", "Read the last notification from the security camera.", "enrollment"),
    SpeakerPhrase("enroll-024", "A bright yellow scarf was folded inside the small suitcase.", "enrollment"),
)


HOLDOUT_VALIDATION_PHRASES: tuple[SpeakerPhrase, ...] = (
    SpeakerPhrase("holdout-001", "Please add blueberries and yogurt to the grocery list.", "holdout_validation"),
    SpeakerPhrase("holdout-002", "Is the upstairs hallway light still on?", "holdout_validation"),
    SpeakerPhrase("holdout-003", "Set a ten minute timer for the pasta.", "holdout_validation"),
    SpeakerPhrase("holdout-004", "Tell me if the mail has arrived today.", "holdout_validation"),
    SpeakerPhrase("holdout-005", "Move tomorrow's meeting from nine thirty to ten.", "holdout_validation"),
    SpeakerPhrase("holdout-006", "Start the coffee maker at seven fifteen in the morning.", "holdout_validation"),
    SpeakerPhrase("holdout-007", "I left my blue jacket beside the small wooden table.", "holdout_validation"),
    SpeakerPhrase("holdout-008", "Read the next message from Alex out loud.", "holdout_validation"),
    SpeakerPhrase("holdout-009", "How long will it take to drive downtown right now?", "holdout_validation"),
    SpeakerPhrase("holdout-010", "Dim the porch lights after sunset.", "holdout_validation"),
    SpeakerPhrase("holdout-011", "Cancel the reminder about watering the plants.", "holdout_validation"),
    SpeakerPhrase("holdout-012", "The silver train crossed the bridge before sunrise.", "holdout_validation"),
    SpeakerPhrase("holdout-013", "Please lower the speaker volume in the office.", "holdout_validation"),
    SpeakerPhrase("holdout-014", "Check whether the back gate was opened today.", "holdout_validation"),
    SpeakerPhrase("holdout-015", "Add black pepper, rice, lemons, and tea to the list.", "holdout_validation"),
    SpeakerPhrase("holdout-016", "What appointments do I have after lunch tomorrow?", "holdout_validation"),
    SpeakerPhrase("holdout-017", "Turn off the fan when the room gets cooler.", "holdout_validation"),
    SpeakerPhrase("holdout-018", "The small red notebook is under the kitchen chair.", "holdout_validation"),
    SpeakerPhrase("holdout-019", "Remind me to charge the camera batteries tonight.", "holdout_validation"),
    SpeakerPhrase("holdout-020", "Play the latest episode in the living room.", "holdout_validation"),
    SpeakerPhrase("holdout-021", "How much time is left on the laundry timer?", "holdout_validation"),
    SpeakerPhrase("holdout-022", "Please tell me the temperature outside.", "holdout_validation"),
    SpeakerPhrase("holdout-023", "Wake me up at six forty five on Saturday.", "holdout_validation"),
    SpeakerPhrase("holdout-024", "The garden hose is coiled beside the garage door.", "holdout_validation"),
)


def active_phrase_set_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "active_version": ACTIVE_SPEAKER_PHRASE_SET_VERSION,
        "phrase_sets": [
            {
                "version": ACTIVE_SPEAKER_PHRASE_SET_VERSION,
                "enrollment": [phrase.to_dict() for phrase in ENROLLMENT_PHRASES],
                "holdout_validation": [phrase.to_dict() for phrase in HOLDOUT_VALIDATION_PHRASES],
                "enrollment_batch_size": 3,
                "minimum_accepted_enrollment_phrases": 8,
                "recommended_accepted_enrollment_phrases": {"min": 12, "max": 16},
                "target_total_speech_duration_ms": {"min": 30000, "max": 60000},
            }
        ],
    }


def select_holdout_phrases(*, count: int = 6, seed: str | None = None) -> dict[str, object]:
    bounded_count = max(1, min(int(count), len(HOLDOUT_VALIDATION_PHRASES)))
    rng = random.Random(seed)
    selected = rng.sample(list(HOLDOUT_VALIDATION_PHRASES), bounded_count)
    return {
        "schema_version": 1,
        "phrase_set_version": ACTIVE_SPEAKER_PHRASE_SET_VERSION,
        "selection_count": bounded_count,
        "seed": seed,
        "phrases": [phrase.to_dict() for phrase in selected],
        "report_attribution": {
            "phrase_set_version": ACTIVE_SPEAKER_PHRASE_SET_VERSION,
            "phrase_category": "holdout_validation",
        },
    }
