"""Exercise the real SDK loop with deterministic delegate models and persistent sessions."""
import asyncio
import json

from drop_ins import edit
from scripted import ScriptedModel, call, say


def interactive(root):
    edit(root, 'scaffold.toml', 'delegates = ["engineer", "reviewer"]', 'delegates = ["reviewer"]')
    with (root / 'scaffold.toml').open('a') as config:
        config.write('\n[runtime]\ntype = "openai-agents"\ninteractive_delegates = true\n')


def tool(runtime, agent, name):
    return next(t for t in runtime.agents[agent].tools if t.name == name)


async def invoke(runtime, name, **args):
    return json.loads(await tool(runtime, 'pm', name).on_invoke_tool(None, json.dumps(args)))


def test_assistant_questions_progress_steering_and_same_session_resume(drop_in_dir, assemble):
    interactive(drop_in_dir)
    researcher = ScriptedModel(call('ask_pi', {'message': 'Which dataset?'}, 'q'),
                               call('report_progress', {'message': 'Read the methods'}, 'p'), say('initial result'),
                               say('revised result'))
    scaffold = assemble(drop_in_dir, {'reviewer': researcher})

    async def run():
        async with scaffold.runtime as runtime:
            started = await invoke(runtime, 'reviewer', brief='Compare methods')
            task_id = started['id']
            await runtime.delegates.tasks[task_id]
            assert not any(t.name in {'send_message', 'interrupt_delegate', 'request_progress'}
                           for t in runtime.agents['reviewer'].tools)
            inbox = await invoke(runtime, 'delegate_inbox', wait_seconds=0)
            question = inbox['tasks'][0]['questions'][0]
            assert question['text'] == 'Which dataset?'
            await invoke(runtime, 'send_message', task_id=task_id, message='Use dataset B',
                         reply_to=question['id'], interrupt=False)
            await runtime.delegates.tasks[task_id]
            result = await invoke(runtime, 'reviewer_result', wait=True)
            assert result['result'] == 'revised result'
            assert result['id'] == task_id
            context = json.dumps(researcher.inputs[-1])
            assert 'Compare methods' in context and 'initial result' in context and 'Use dataset B' in context
            assert scaffold.context.usage.by_source['reviewer'].requests == 4
    asyncio.run(run())


def test_interrupt_progress_and_restart_preserve_identity(drop_in_dir, assemble):
    interactive(drop_in_dir)
    researcher = ScriptedModel(say('resumed'))
    scaffold = assemble(drop_in_dir, {'reviewer': researcher})

    async def run():
        active = asyncio.Event()
        original = researcher.get_response

        async def blocked(*args, **kwargs):
            active.set()
            await asyncio.Future()

        researcher.get_response = blocked
        async with scaffold.runtime as runtime:
            task = await invoke(runtime, 'reviewer', brief='Keep this brief')
            await active.wait()
            requested = await invoke(runtime, 'request_progress', task_id=task['id'])
            assert requested['kind'] == 'request_progress'
            stopped = await invoke(runtime, 'interrupt_delegate', task_id=task['id'])
            assert stopped['status'] == 'interrupted'
            assert runtime.delegates.tasks[task['id']].done()
        researcher.get_response = original
        restarted = assemble(drop_in_dir, {'reviewer': researcher})
        async with restarted.runtime as runtime:
            await invoke(runtime, 'send_message', task_id=task['id'], message='Continue with tests',
                         reply_to=None, interrupt=False)
            result = await invoke(runtime, 'reviewer_result', wait=True)
            assert result['id'] == task['id'] and result['result'] == 'resumed'
            assert 'Keep this brief' in json.dumps(researcher.inputs[-1])
            assert 'report_progress' in json.dumps(researcher.inputs[-1])
    asyncio.run(run())


def test_upward_mail_cannot_preempt_pi_and_wait_returns_for_question(drop_in_dir, assemble):
    interactive(drop_in_dir)
    pm = ScriptedModel(say('done'))
    scaffold = assemble(drop_in_dir, {'pm': pm})

    async def run():
        async with scaffold.runtime as runtime:
            entered, release = asyncio.Event(), asyncio.Event()
            original = pm.get_response

            async def model(*args, **kwargs):
                entered.set()
                await release.wait()
                return await original(*args, **kwargs)

            pm.get_response = model

            async def delegate(task, prompt):
                await asyncio.Future()
            runtime.delegates.execute = delegate
            task = await invoke(runtime, 'reviewer', brief='Investigate')
            pi_turn = asyncio.create_task(runtime.run('Do work', workflow='test'))
            await entered.wait()
            runtime.delegates.mailboxes.delegate(task['id']).ask_pi('Which control?')
            await asyncio.sleep(.02)
            assert not pi_turn.done() and pm.inputs == []
            result = await asyncio.wait_for(invoke(runtime, 'reviewer_result', wait=True), .5)
            assert result['status'] == 'running' and not result['collected']
            release.set()
            outcome = await pi_turn
            assert not outcome.completed and 'outstanding' in outcome.final_output
            assert len(pm.inputs) == 1
    asyncio.run(run())


def test_pending_pi_message_is_in_every_active_delegate_call(drop_in_dir, assemble):
    interactive(drop_in_dir)
    researcher = ScriptedModel(call('read_file', {'path': 'AGENTS.md'}, 'read'), say('done'))
    scaffold = assemble(drop_in_dir, {'reviewer': researcher})

    async def run():
        async with scaffold.runtime as runtime:
            task = await invoke(runtime, 'reviewer', brief='Inspect')
            await invoke(runtime, 'send_message', task_id=task['id'], message='Prioritize evidence',
                         reply_to=None, interrupt=False)
            await runtime.delegates.tasks[task['id']]
            assert len(researcher.inputs) == 2
            assert all('Prioritize evidence' in json.dumps(items) for items in researcher.inputs)
            assert not runtime.delegates.mailboxes.pending('delegate', task['id'])
    asyncio.run(run())


def test_mail_arriving_during_final_response_gets_a_followup_turn(drop_in_dir, assemble):
    interactive(drop_in_dir)
    researcher = ScriptedModel(say('old result'), say('updated result'))
    scaffold = assemble(drop_in_dir, {'reviewer': researcher})

    async def run():
        async with scaffold.runtime as runtime:
            original = researcher.get_response
            async def respond(*args, **kwargs):
                result = await original(*args, **kwargs)
                if len(researcher.inputs) == 1:
                    task = runtime.delegates.mailboxes.latest('reviewer')
                    await invoke(runtime, 'send_message', task_id=task['id'], message='Review the new evidence',
                                 reply_to=None, interrupt=False)
                return result
            researcher.get_response = respond
            task = await invoke(runtime, 'reviewer', brief='Analyze')
            await runtime.delegates.tasks[task['id']]
            result = await invoke(runtime, 'reviewer_result', wait=False)
            assert result['result'] == 'updated result'
            assert 'Review the new evidence' in json.dumps(researcher.inputs[-1])
    asyncio.run(run())
