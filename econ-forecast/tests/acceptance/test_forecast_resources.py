"""Resource acceptance entry points; benchmarks never silently skip as passed."""
import json
import os
from pathlib import Path
import subprocess
import time
import psutil
import pytest


@pytest.mark.slow
def test_frozen_reference_resource_benchmark(
        tmp_path, resource_dataset_factory, forecast_config_factory):
    dataset=resource_dataset_factory(tmp_path/"reference input")
    config=forecast_config_factory(tmp_path/"reference forecast.yaml",dataset,
        default_org_id="benchmark-org",scopes=[{"type":"organization"}],
        uncertainty={"paths":1000,"seed":42})
    root=Path(__file__).parents[3]
    def execute(label):
        output=tmp_path/f"{label} output"; stdout_path=tmp_path/f"{label}.stdout"; stderr_path=tmp_path/f"{label}.stderr"
        command=[str(root/"econ-forecast/utils/forecast.sh"),"--config",str(config),"--origin","2026-06-02",
                 "--unit","month","--offset","1","--periods","2","--output",str(output)]
        started=time.perf_counter(); peak=0; memory_samples=0
        with stdout_path.open("w") as stdout_stream, stderr_path.open("w") as stderr_stream:
            process=subprocess.Popen(command,text=True,stdout=stdout_stream,stderr=stderr_stream,start_new_session=True)
            monitored=psutil.Process(process.pid)
            while process.poll() is None:
                if time.perf_counter()-started>305:
                    os.killpg(process.pid,15); process.wait(timeout=10)
                    pytest.fail(f"{label} forecast exceeded 300-second target; stderr at {stderr_path}")
                try:
                    peak=max(peak,sum(p.memory_info().rss for p in [monitored,*monitored.children(recursive=True)]))
                    memory_samples += 1
                except (psutil.NoSuchProcess,psutil.AccessDenied): pass
                time.sleep(.05)
        wall=time.perf_counter()-started; stderr=stderr_path.read_text(); stdout=stdout_path.read_text()
        assert process.returncode==0,stderr
        summary=json.loads(stdout); output_bytes=sum(p.stat().st_size for p in output.rglob('*') if p.is_file())
        assert memory_samples > 0 and peak > 0, "process-tree RSS was not measured"
        assert summary["status"]=="complete" and wall<=300 and peak<=2*1024**3 and output_bytes<=200*1024**2
        index=json.loads((output/"paths/index.json").read_text())
        assert len(index["series"])==100 and len(index["dates"])==62 and len(index["sample_ids"])==1000
        assert all(entry["shape"]==[62,1000] for entry in index["series"])
        return {"label":label,"wall_seconds":wall,"sampled_process_tree_rss_bytes":peak,
                "output_bytes":output_bytes,"output":str(output)}
    evidence=[execute("cold"),execute("repeat")]
    usage=dataset/"usage.jsonl"; coverage=dataset/"coverage.jsonl"
    measured={"usage_rows":len(usage.read_bytes().splitlines()),"coverage_rows":len(coverage.read_bytes().splitlines()),
              "usage_bytes":usage.stat().st_size,"coverage_bytes":coverage.stat().st_size}
    assert measured["usage_rows"]==measured["coverage_rows"]==9000
    print(json.dumps({"runs":evidence,**measured},sort_keys=True))
