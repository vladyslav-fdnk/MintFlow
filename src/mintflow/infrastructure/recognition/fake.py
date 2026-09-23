"""A deterministic recognizer for tests and local development; never used in production."""

from dataclasses import dataclass, field

from mintflow.application.receipts import RecognitionOutput, RecognitionUnavailable


@dataclass
class FakeReceiptRecognizer:
    """Returns ``outputs[image]`` or ``default``; images in ``failing`` raise.

    ``calls`` records every image it was asked to read.
    """

    default: RecognitionOutput = field(default_factory=RecognitionOutput)
    outputs: dict[bytes, RecognitionOutput] = field(default_factory=dict)
    failing: set[bytes] = field(default_factory=set)
    calls: list[bytes] = field(default_factory=list)

    def recognize(self, *, image: bytes, media_type: str) -> RecognitionOutput:
        self.calls.append(image)
        if image in self.failing:
            raise RecognitionUnavailable("fake recognizer failure")
        return self.outputs.get(image, self.default)
