from app import realtime_pb2 as pb


def test_proto_roundtrip():
    msg = pb.ServiceBoundMessage(
        user_input=pb.UserInput(
            packet_id=7,
            mode=pb.QUEUE,
            audio_data=pb.AudioData(data=b"\x00\x01"),
        )
    )
    parsed = pb.ServiceBoundMessage.FromString(msg.SerializeToString())
    assert parsed.user_input.packet_id == 7
    assert parsed.user_input.audio_data.data == b"\x00\x01"
    assert parsed.WhichOneof("payload") == "user_input"


def test_client_bound_tool_call():
    from google.protobuf import struct_pb2

    params = struct_pb2.Struct()
    params.update({"area": "bedroom", "action": "on"})
    msg = pb.ClientBoundMessage(
        tool_call_request=pb.ToolCallRequest(id="c1", name="control_lights", parameters=params)
    )
    parsed = pb.ClientBoundMessage.FromString(msg.SerializeToString())
    assert parsed.WhichOneof("payload") == "tool_call_request"
    assert dict(parsed.tool_call_request.parameters)["area"] == "bedroom"
