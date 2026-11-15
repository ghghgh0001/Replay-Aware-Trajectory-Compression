"""Read original ROS1 stamped pose messages, without clock resampling."""

from __future__ import annotations

import bz2
import io
import struct

import numpy as np


U32 = struct.Struct("<I")
POSE = struct.Struct("<7d")


def read_exact(stream, count):
    if not 0 <= count <= 256 * 1024 * 1024:
        raise ValueError("Unexpected ROS bag record extent")
    value = stream.read(count)
    if len(value) != count:
        raise ValueError("Truncated ROS bag record")
    return value


def fields(value):
    result, offset = {}, 0
    while offset < len(value):
        if offset + 4 > len(value):
            raise ValueError("Truncated ROS field length")
        length = U32.unpack_from(value, offset)[0]
        offset += 4
        if offset + length > len(value):
            raise ValueError("Truncated ROS field")
        field = value[offset:offset + length]
        offset += length
        if b"=" not in field:
            raise ValueError("Malformed ROS field")
        key, content = field.split(b"=", 1)
        if key in result:
            raise ValueError("Duplicate ROS field")
        result[key] = content
    return result


def records(stream):
    while True:
        first = stream.read(4)
        if not first:
            return
        if len(first) != 4:
            raise ValueError("Truncated ROS record directory")
        header = fields(read_exact(stream, U32.unpack(first)[0]))
        content = read_exact(stream, U32.unpack(read_exact(stream, 4))[0])
        yield header, content


def string_at(content, offset):
    if offset + 4 > len(content):
        raise ValueError("Missing ROS string extent")
    length = U32.unpack_from(content, offset)[0]
    offset += 4
    if length > 4096 or offset + length > len(content):
        raise ValueError("Invalid ROS frame string")
    return content[offset:offset + length].decode("utf-8"), offset + length


def transform(content, message_type="geometry_msgs/TransformStamped"):
    minimum = 20 if message_type == "geometry_msgs/TransformStamped" else 16
    if len(content) < minimum + POSE.size:
        raise ValueError("Truncated stamped pose")
    sequence, seconds, nanoseconds = struct.unpack_from("<III", content)
    if nanoseconds >= 1_000_000_000:
        raise ValueError("Invalid ROS nanosecond field")
    parent, offset = string_at(content, 12)
    if message_type == "geometry_msgs/TransformStamped":
        child, offset = string_at(content, offset)
    elif message_type == "geometry_msgs/PoseStamped":
        child = "not_in_PoseStamped"
    else:
        raise ValueError("Unsupported stamped pose schema")
    if offset + POSE.size != len(content):
        raise ValueError("Unexpected stamped pose extent")
    values = POSE.unpack_from(content, offset)
    if not np.isfinite(values).all():
        raise ValueError("Nonfinite raw Vicon pose")
    return sequence, seconds * 1_000_000_000 + nanoseconds, values[:3], values[3:], parent, child


def load_rosbag(path, topic="/vicon/blackbird"):
    connections, frames = {}, set()
    stamps, positions, quaternions, sequences = [], [], [], []
    chunks = 0

    def consume(header, content):
        operation = header.get(b"op")
        if operation == b"\x07":
            identifier = U32.unpack(header[b"conn"])[0]
            definition = fields(content)
            connections[identifier] = {"topic": header[b"topic"].decode(),
                                       "type": definition[b"type"].decode()}
        elif operation == b"\x02":
            identifier = U32.unpack(header[b"conn"])[0]
            if identifier not in connections:
                raise ValueError("ROS message arrived before its connection definition")
            connection = connections[identifier]
            if connection["topic"] != topic:
                return
            if connection["type"] not in ("geometry_msgs/TransformStamped", "geometry_msgs/PoseStamped"):
                raise ValueError("Unexpected raw Vicon message type")
            sequence, stamp, position, quaternion, parent, child = transform(content, connection["type"])
            stamps.append(stamp)
            positions.append(position)
            quaternions.append(quaternion)
            sequences.append(sequence)
            frames.add((parent, child))

    with path.open("rb") as stream:
        if read_exact(stream, 13) != b"#ROSBAG V2.0\n":
            raise ValueError("Only ROS bag V2.0 is supported")
        for header, content in records(stream):
            if header.get(b"op") == b"\x05":
                compression = header[b"compression"]
                if compression == b"bz2":
                    content = bz2.decompress(content)
                elif compression != b"none":
                    raise ValueError("Unverified raw bag chunk compression")
                if len(content) != U32.unpack(header[b"size"])[0]:
                    raise ValueError("ROS chunk decompression extent mismatch")
                chunks += 1
                for inner_header, inner_content in records(io.BytesIO(content)):
                    consume(inner_header, inner_content)
            else:
                consume(header, content)
    if len(stamps) < 2 or len(frames) != 1:
        raise ValueError("Expected consistent raw Vicon position stream")
    stamps = np.asarray(stamps, np.int64)
    return (stamps - stamps[0]).astype(float) * 1e-9, np.asarray(positions), {
        "time_origin_ns": int(stamps[0]), "clock": "original_ROS_message_header_time",
        "topic": topic, "message_type": [definition["type"] for definition in connections.values()
                                          if definition["topic"] == topic],
        "frame_ids": [list(frame) for frame in sorted(frames)],
        "chunks": chunks, "source_reports_removed_or_interpolated": 0,
        "sequence_nonunit_intervals": int(np.count_nonzero(np.diff(sequences) != 1)),
        "quaternion_xyzw_norm_min": float(np.linalg.norm(quaternions, axis=1).min()),
        "quaternion_xyzw_norm_max": float(np.linalg.norm(quaternions, axis=1).max())}
