"""Reserve inference and wall time for synthesis, using the existing phased loop."""

import asyncio
from contextlib import suppress

from pydantic import Field

from crux_scaffold.errors import InvalidDropInError
from crux_scaffold.loop import LOOPS, LoopOutcome, PhasedLoop, PhasedOptions


class ResearchOptions(PhasedOptions):
    reserve_wall_seconds: float = Field(7200, ge=0, allow_inf_nan=False)
    reserve_fraction: float = Field(0.1, ge=0, lt=1)


@LOOPS.register
class ResearchLoop(PhasedLoop):
    type_name = "vf_research"
    Options = ResearchOptions

    def __init__(self, name, options, *, gates):
        super().__init__(name, options, gates=gates)
        if [phase.name for phase in options.phases] != ["research", "finalize"]:
            raise InvalidDropInError("vf_research requires research then finalize phases")

    async def run(self, runtime, drop_in, ctx, state, store):
        task = None
        try:
            while True:
                await ctx.refresh_resources()
                elapsed = ctx.budget.elapsed(ctx.usage, ctx.now()) or 0
                wall = ctx.budget.max_wall_seconds
                if ctx.budget_exhausted():
                    return LoopOutcome(status="budget_exhausted", phase=self.options.phases[
                        min(state.phase_index, 1)].name, final_output="Run budget reached; state preserved.")
                cost = ctx.model_usage()["model_cost_usd"]
                cap = ctx.budget.max_cost_usd
                reserve = ((wall is not None and elapsed >= wall - self.options.reserve_wall_seconds)
                           or (cap is not None and cost is not None
                               and cost >= cap * (1 - self.options.reserve_fraction)))
                if state.phase_index == 0 and reserve:
                    if task:
                        task.cancel()
                        with suppress(asyncio.CancelledError):
                            await task
                        task = None
                    state.phase_index, state.iteration, state.next_prompt = 1, 0, None
                    store.save(state)
                if task is None:
                    task = asyncio.create_task(super().run(runtime, drop_in, ctx, state, store))
                done, _ = await asyncio.wait([task], timeout=15)
                if done:
                    return await task
        finally:
            if task and not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            store.save(state)
