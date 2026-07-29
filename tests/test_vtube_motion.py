import asyncio

from bridge.vtube_motion import VTubeMotionEngine, build_motion_capabilities


def capability_payloads():
    input_payload = {
        "modelLoaded": True,
        "modelName": "Test Model",
        "modelID": "model-1",
        "defaultParameters": [
            {
                "name": "FaceAngleX",
                "value": 0,
                "min": -30,
                "max": 30,
                "defaultValue": 0,
            },
            {
                "name": "FaceAngleY",
                "value": 0,
                "min": -30,
                "max": 30,
                "defaultValue": 0,
            },
            {
                "name": "FaceAngleZ",
                "value": 0,
                "min": -30,
                "max": 30,
                "defaultValue": 0,
            },
            {
                "name": "FacePositionY",
                "value": 0,
                "min": -10,
                "max": 10,
                "defaultValue": 0,
            },
            {
                "name": "EyeOpenLeft",
                "value": 1,
                "min": 0,
                "max": 1,
                "defaultValue": 1,
            },
            {
                "name": "EyeOpenRight",
                "value": 1,
                "min": 0,
                "max": 1,
                "defaultValue": 1,
            },
            {
                "name": "MouthSmile",
                "value": 0,
                "min": 0,
                "max": 1,
                "defaultValue": 0,
            },
        ],
        "customParameters": [],
    }
    live2d_payload = {
        "modelLoaded": True,
        "modelName": "Test Model",
        "modelID": "model-1",
        "parameters": [
            {
                "name": "ParamAngleX",
                "value": 0,
                "min": -30,
                "max": 30,
                "defaultValue": 0,
            }
        ],
    }
    expression_payload = {
        "modelLoaded": True,
        "modelName": "Test Model",
        "modelID": "model-1",
        "expressions": [
            {
                "name": "happy_smile",
                "file": "happy_smile.exp3.json",
                "active": False,
            },
            {
                "name": "surprised",
                "file": "surprised.exp3.json",
                "active": False,
            },
        ],
    }
    return input_payload, live2d_payload, expression_payload


def test_motion_capability_scan_resolves_standard_parameters_and_expressions():
    result = build_motion_capabilities(*capability_payloads())

    assert result["model_loaded"] is True
    assert result["role_map"]["angle_x"] == "FaceAngleX"
    assert result["role_map"]["mouth_smile"] == "MouthSmile"
    assert "talking" in result["supported_actions"]
    assert "wave" in result["supported_actions"]
    assert result["recommended_motion_engine"]["expression_map"]["happy"] == (
        "happy_smile.exp3.json"
    )
    assert result["recommended_motion_engine"]["expression_map"]["surprised"] == (
        "surprised.exp3.json"
    )


def test_motion_engine_injects_idle_talking_and_semantic_actions():
    capabilities = build_motion_capabilities(*capability_payloads())

    class FakeClient:
        def __init__(self):
            self.voice = 0.0
            self.injected = []
            self.expressions = []

        async def parameter_value(self, _name):
            return self.voice

        async def inject_parameters(self, values):
            self.injected.append(dict(values))
            return {"injected": len(values)}

        async def activate_expression(self, file_name, *, active, fade_time=0.25):
            self.expressions.append((file_name, active, fade_time))
            return {"expression_file": file_name, "active": active}

    async def scenario():
        client = FakeClient()
        engine = VTubeMotionEngine(
            client,
            {
                "enabled": True,
                "fps": 20,
                "speech_threshold": 0.05,
                "expression_map": {"happy": "happy_smile.exp3.json"},
            },
            capabilities,
        )
        await engine.start()
        await asyncio.sleep(0.14)
        assert client.injected
        assert engine.status()["current_mode"] == "idle"

        client.voice = 0.8
        await asyncio.sleep(0.2)
        assert engine.status()["speaking"] is True
        assert engine.status()["current_mode"] == "talking"

        result = await engine.trigger("happy")
        await asyncio.sleep(0.08)
        assert result["procedural"] is True
        assert engine.status()["transient_action"] == "happy"
        assert ("happy_smile.exp3.json", True, 0.2) in client.expressions

        await engine.stop(reset=True)
        assert engine.status()["running"] is False

    asyncio.run(scenario())
