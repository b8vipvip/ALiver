import asyncio

from bridge.vtube_motion import VTubeMotionEngine, build_motion_capabilities


def capabilities():
    inputs = {
        "modelLoaded": True,
        "defaultParameters": [
            {"name": "FaceAngleX", "value": 0, "min": -30, "max": 30, "defaultValue": 0},
            {"name": "FaceAngleY", "value": 0, "min": -30, "max": 30, "defaultValue": 0},
            {"name": "FaceAngleZ", "value": 0, "min": -30, "max": 30, "defaultValue": 0},
            {"name": "FacePositionY", "value": 0, "min": -10, "max": 10, "defaultValue": 0},
            {"name": "BrowLeftY", "value": 0, "min": -1, "max": 1, "defaultValue": 0},
            {"name": "BrowRightY", "value": 0, "min": -1, "max": 1, "defaultValue": 0},
            {"name": "MouthSmile", "value": 0, "min": 0, "max": 1, "defaultValue": 0},
        ],
        "customParameters": [],
    }
    live2d = {
        "modelLoaded": True,
        "parameters": [
            {"name": "ParamMouthOpenY", "value": 0, "min": 0, "max": 1, "defaultValue": 0}
        ],
    }
    expressions = {"modelLoaded": True, "expressions": []}
    return build_motion_capabilities(inputs, live2d, expressions)


class FakeClient:
    def __init__(self, *, voice=0.0, mouth=0.0):
        self.voice = voice
        self.mouth = mouth

    async def parameter_value(self, _name):
        return self.voice

    async def live2d_parameters(self):
        return {
            "modelLoaded": True,
            "parameters": [
                {
                    "name": "ParamMouthOpenY",
                    "value": self.mouth,
                    "min": 0,
                    "max": 1,
                    "defaultValue": 0,
                }
            ],
        }

    async def inject_parameters(self, values):
        return {"injected": len(values)}

    async def activate_expression(self, *_args, **_kwargs):
        return {}


def test_talking_motion_is_clearly_stronger_than_idle_motion():
    engine = VTubeMotionEngine(
        FakeClient(voice=0.7),
        {
            "enabled": True,
            "preset": "lively",
            "idle_intensity": 0.55,
            "talking_intensity": 0.85,
            "speech_threshold": 0.08,
        },
        capabilities(),
    )
    engine._voice_value = 0.7
    engine._speech_started_monotonic = engine._started_monotonic
    at = engine._started_monotonic + 0.42

    idle = engine._values_for("idle", at)
    talking = engine._values_for("talking", at)

    idle_energy = sum(abs(idle.get(name, 0.0)) for name in ("FaceAngleX", "FaceAngleY", "FaceAngleZ"))
    talking_energy = sum(
        abs(talking.get(name, 0.0)) for name in ("FaceAngleX", "FaceAngleY", "FaceAngleZ")
    )
    assert talking_energy > idle_energy * 2
    assert engine.status()["algorithm_version"] == 2
    assert engine.status()["speech_motion_gain"] > 1


def test_mouth_output_fallback_can_trigger_talking_mode():
    async def scenario():
        engine = VTubeMotionEngine(
            FakeClient(voice=0.0, mouth=0.65),
            {
                "enabled": True,
                "speech_threshold": 0.08,
                "speech_hold_ms": 500,
            },
            capabilities(),
        )
        await engine._poll_voice(10.0)

        status = engine.status()
        assert status["speaking"] is True
        assert status["speech_signal_source"] == "live2d_mouth"
        assert status["voice_value"] == 0.65

    asyncio.run(scenario())
