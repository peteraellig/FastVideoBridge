"""Bounded in-memory timings; no per-frame file logging."""
from collections import deque
import time

class Timings:
    def __init__(self):
        self.samples = {}
    def add(self, stage, started):
        self.samples.setdefault(stage, deque(maxlen=250)).append((time.perf_counter()-started)*1000)
    def snapshot(self):
        result = {}
        for stage, values in list(self.samples.items()):
            ordered = sorted(list(values))
            if ordered:
                result[stage] = {'mean_ms':round(sum(ordered)/len(ordered),3),
                    'p95_ms':round(ordered[min(len(ordered)-1,int(len(ordered)*.95))],3),
                    'max_ms':round(ordered[-1],3),'samples':len(ordered)}
        return result
