"""One dedicated, backpressured worker per output media stream."""
import asyncio
import time
from concurrent.futures import ThreadPoolExecutor

class OutputWorkers:
    def __init__(self):
        self.executors={kind:ThreadPoolExecutor(max_workers=1,thread_name_prefix='bridge-out-'+kind)
                        for kind in ('video','audio')}

    async def call(self,kind,callback,timings,*args):
        submitted=time.perf_counter()
        def execute():
            timings.add(kind+'_worker_dispatch',submitted)
            started=time.perf_counter()
            result=callback(*args)
            timings.add(kind+'_worker_processing',started)
            return result,time.perf_counter()
        # Consumer awaits every submission: there is never an unbounded backlog.
        result,finished=await asyncio.get_running_loop().run_in_executor(self.executors[kind],execute)
        timings.add(kind+'_event_loop_resume',finished)
        return result

    async def drain(self):
        # Cancelled consumers may leave a native call running; barrier before
        # reconnect or destroy keeps buffers and sender handles alive until done.
        loop=asyncio.get_running_loop()
        await asyncio.gather(*(loop.run_in_executor(e,lambda:None) for e in self.executors.values()))

    def close(self):
        for executor in self.executors.values():executor.shutdown(wait=False)
