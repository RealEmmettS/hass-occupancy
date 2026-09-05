"""Build-time native CPU validation; no HA API or household data involved."""
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from sklearn.ensemble import HistGradientBoostingRegressor

with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / "smoke.parquet"
    frame = pd.DataFrame({"occupancy": [0.0, 1.0, 0.5]})
    frame.to_parquet(path)
    assert pd.read_parquet(path).equals(frame)
    assert pq.read_metadata(path).num_rows == 3
x = np.arange(160, dtype=float).reshape(80, 2)
y = (x[:, 0] % 8) / 8
model = HistGradientBoostingRegressor(max_iter=2, max_leaf_nodes=3).fit(x, y)
assert np.isfinite(model.predict(x)).all()
print("Native CPU smoke passed: numpy", np.__version__, "pandas", pd.__version__,
      "pyarrow", pa.__version__, "Parquet round-trip and model fit/predict", flush=True)
