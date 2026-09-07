import pytest
from pydantic import ValidationError

from hexevoice.api.models import (
    EndpointPlaySoundCommandRequest,
    EndpointSpeakCommandRequest,
    TTS_TEXT_MAX_LENGTH,
    TtsSynthesizeRequest,
)


def test_tts_text_requests_allow_larger_responses():
    text = "x" * TTS_TEXT_MAX_LENGTH

    TtsSynthesizeRequest(text=text)
    EndpointSpeakCommandRequest(endpoint_id="esp-pe-1", text=text)
    EndpointPlaySoundCommandRequest(endpoint_id="esp-pe-1", text=text)


def test_tts_text_requests_still_reject_unbounded_responses():
    text = "x" * (TTS_TEXT_MAX_LENGTH + 1)

    with pytest.raises(ValidationError):
        TtsSynthesizeRequest(text=text)
    with pytest.raises(ValidationError):
        EndpointSpeakCommandRequest(endpoint_id="esp-pe-1", text=text)
    with pytest.raises(ValidationError):
        EndpointPlaySoundCommandRequest(endpoint_id="esp-pe-1", text=text)
