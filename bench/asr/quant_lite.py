"""Low-memory dynamic int8 for the fp32 encoder: the node pattern ORT's quantize_dynamic writes (IntegerOps,
symmetric int8 per-tensor weights, uint8 dynamic activations), applied to MatMuls with a constant weight,
reading each weight from the external data file one at a time."""
import sys
from pathlib import Path

import numpy as np
import onnx
from onnx import TensorProto as T, helper, numpy_helper

f32, out = Path(sys.argv[1]), Path(sys.argv[2])
model = onnx.load(f32, load_external_data=False)
g = model.graph
data = np.memmap(f32.with_suffix(".data"), dtype=np.uint8, mode="r")
inits = {i.name: i for i in g.initializer}


def ext(init):
    info = {e.key: e.value for e in init.external_data}
    off, n = int(info.get("offset", 0)), int(info["length"])
    return np.frombuffer(data[off:off + n], dtype=np.float32).reshape(tuple(init.dims))


new_nodes, quantized, kept = [], set(), 0
for node in g.node:
    w = inits.get(node.input[1]) if node.op_type == "MatMul" else None
    if w is None or w.data_type != T.FLOAT:
        new_nodes.append(node)
        continue
    arr = ext(w) if w.data_location == T.EXTERNAL else numpy_helper.to_array(w)
    scale = np.float32(max(np.abs(arr).max(), 1e-8) / 127.0)
    q = np.clip(np.rint(arr / scale), -127, 127).astype(np.int8)
    b = w.name
    g.initializer.append(numpy_helper.from_array(q, b + "_q"))
    g.initializer.append(numpy_helper.from_array(np.array(scale, np.float32), b + "_s"))
    g.initializer.append(numpy_helper.from_array(np.array(0, np.int8), b + "_zp"))
    a, y, p = node.input[0], node.output[0], node.name or y
    new_nodes += [
        helper.make_node("DynamicQuantizeLinear", [a], [p + "_aq", p + "_as", p + "_azp"], p + "_dql"),
        helper.make_node("MatMulInteger", [p + "_aq", b + "_q", p + "_azp", b + "_zp"], [p + "_i32"], p + "_mmi"),
        helper.make_node("Cast", [p + "_i32"], [p + "_f"], p + "_cast", to=T.FLOAT),
        helper.make_node("Mul", [p + "_as", b + "_s"], [p + "_sc"], p + "_mulsc"),
        helper.make_node("Mul", [p + "_f", p + "_sc"], [y], p + "_mul"),
    ]
    quantized.add(b)

users = {i for n in new_nodes for i in n.input}
keep = []
for init in g.initializer:
    if init.name in quantized and init.name not in users:
        continue
    if init.data_location == T.EXTERNAL:
        arr = ext(init)
        init.ClearField("external_data")
        init.data_location = T.DEFAULT
        init.raw_data = arr.tobytes()
        kept += arr.size
    keep.append(init)
g.ClearField("initializer")
g.initializer.extend(keep)
g.ClearField("node")
g.node.extend(new_nodes)
g.ClearField("value_info")
if not any(o.domain in ("", "ai.onnx") and o.version >= 11 for o in model.opset_import):
    raise SystemExit("needs opset 11+")
onnx.save(model, out)
print(f"quantized {len(quantized)} MatMul weights; {kept / 1e6:.1f}M external weights kept as fp32;",
      f"{out.stat().st_size / 1e6:.0f} MB")
