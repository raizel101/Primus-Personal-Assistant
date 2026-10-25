"""Formal search semantics, honest limits, source scoping and shared-core routing."""
from __future__ import annotations
import copy
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "vendor"))
sys.path.insert(0, str(PROJECT / "src"))
from primus.planning import (PlanningError, load_problem, parse_problem, plan_file, replay,
                             run_plugin, solve, validate_file)
from primus.plugins import discover
from primus.service import TaskRunner
from primus.storage import Store
from primus.tool_router import select_plugins


def domain():
    return {"name": "Move a package", "facts": ["at_a", "at_b", "at_c", "delivered"],
            "initial": ["at_a"], "goal": ["delivered"], "actions": [
                {"name": "to_b", "preconditions": ["at_a"], "add": ["at_b"], "delete": ["at_a"]},
                {"name": "to_c", "preconditions": ["at_b"], "add": ["at_c"], "delete": ["at_b"]},
                {"name": "deliver_c", "preconditions": ["at_c"], "add": ["delivered"], "delete": []},
                {"name": "deliver_b", "preconditions": ["at_b"], "add": ["delivered"], "delete": []}]}


class PlanningTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(dir=PROJECT / "temp")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "logs").mkdir()
        (self.root / "data").mkdir()
        self.path = self.root / "problem with spaces.json"
        self.path.write_text(json.dumps(domain()), encoding="utf-8")
        self.config = {"_root": self.root, "approved_folders": [],
                       "permissions": {"read": "auto", "write": "approved_folders"},
                       "model": {"name": "unused", "endpoint": "http://127.0.0.1:11434/api/chat"}}

    def test_bfs_finds_shortest_plan_with_delete_effects(self):
        problem = parse_problem(domain())
        result = solve(problem)
        self.assertEqual(result["plan"], ["to_b", "deliver_b"])
        self.assertTrue(result["shortest_unit_cost_plan"])
        self.assertTrue(result["validation"]["valid"])
        self.assertNotIn("at_a", result["validation"]["final_state"])
        self.assertFalse(result["executed"])
        self.assertEqual(result["validation"]["trace"][0]["delete"], ["at_a"])

    def test_replay_rejects_bad_order_unknown_action_and_incomplete_goal(self):
        problem = parse_problem(domain())
        wrong = replay(problem, ["deliver_c"])
        self.assertEqual(wrong["failed_step"], 1)
        self.assertEqual(wrong["missing_preconditions"], ["at_c"])
        self.assertFalse(replay(problem, ["install_package"])["valid"])
        self.assertEqual(replay(problem, ["to_b"])["reason"], "Goal not reached")
        self.assertTrue(replay(problem, ["to_b", "deliver_b"])["valid"])

    def test_goal_already_true_and_empty_goal_need_no_action(self):
        for goal in (["at_a"], []):
            value = domain(); value["goal"] = goal
            result = solve(parse_problem(value))
            self.assertEqual(result["plan"], [])
            self.assertTrue(result["validation"]["valid"])

    def test_cycles_terminate_and_unreachable_is_exhaustive(self):
        value = domain()
        value["actions"] = [value["actions"][0],
                            {"name": "back", "preconditions": ["at_b"], "add": ["at_a"], "delete": ["at_b"]}]
        result = solve(parse_problem(value))
        self.assertEqual(result["status"], "unreachable")
        self.assertEqual(result["visited_states"], 2)
        self.assertIsNone(result["plan"])

    def test_state_and_depth_caps_do_not_claim_unreachable(self):
        for limits in ({"max_states": 1}, {"max_depth": 1}):
            result = solve(parse_problem(domain()), **limits)
            self.assertEqual(result["status"], "limit_reached")
            self.assertIsNone(result["validation"])
        value=domain();value["actions"]=[]
        self.assertEqual(solve(parse_problem(value), max_depth=0)["status"], "unreachable")

    def test_found_goal_is_not_lost_at_state_cap(self):
        value = domain(); value["goal"] = ["at_b"]
        self.assertEqual(solve(parse_problem(value), max_states=2)["status"], "solved")

    def test_time_budget_and_invalid_limits(self):
        with patch("primus.planning.time.monotonic", side_effect=[0.0, 3.0, 3.0]):
            self.assertEqual(solve(parse_problem(domain()))["status"], "limit_reached")
        for limits in ({"max_states": True}, {"max_depth": 17}, {"max_seconds": float("nan")}, {"max_seconds": 10}):
            with self.assertRaises(PlanningError): solve(parse_problem(domain()), **limits)

    def test_schema_refuses_ambiguous_or_undeclared_semantics(self):
        bad=[]
        v=domain();v["initial"].append("undeclared");bad.append(v)
        v=domain();v["actions"][0]["delete"].append("at_b");bad.append(v)
        v=domain();v["actions"].append(copy.deepcopy(v["actions"][0]));bad.append(v)
        v=domain();v["facts"].append("at_a");bad.append(v)
        v=domain();v["actions"][0]["command"]="echo unsafe";bad.append(v)
        v=domain();v["actions"][0]["preconditions"]=[{"not":"at_c"}];bad.append(v)
        v=domain();v["facts"]=[f"f{i}" for i in range(33)];bad.append(v)
        for value in bad:
            with self.subTest(value=value), self.assertRaises(PlanningError): parse_problem(value)

    def test_file_limits_duplicate_keys_and_path_scope(self):
        self.path.write_text('{"name":"first","name":"second"}',encoding="utf-8")
        with self.assertRaises(PlanningError): load_problem(self.config,str(self.path))
        self.path.write_bytes(b" "*65537)
        with self.assertRaises(PlanningError): load_problem(self.config,str(self.path))
        with self.assertRaises(PermissionError): load_problem(self.config,str(PROJECT/'examples/planning/downloads.json'))
        self.config["approved_folders"]=[str(PROJECT/'examples')]
        problem,_=load_problem(self.config,str(PROJECT/'examples/planning/downloads.json'))
        self.assertEqual(solve(problem)["status"],"solved")
        with self.assertRaises(PermissionError): load_problem(self.config,str(self.root/'.ssh/key.json'))

    def test_kill_switch_and_cancellation_stop_search_and_replay(self):
        (self.root/'data/STOP').touch()
        with self.assertRaises(RuntimeError): plan_file(self.config, {"path":str(self.path)})
        (self.root/'data/STOP').unlink()
        cancel=threading.Event();cancel.set();self.config['_cancel_event']=cancel
        with self.assertRaises(RuntimeError): validate_file(self.config,{"path":str(self.path),"actions":[]})
        calls=[]
        def check():
            calls.append(1)
            if len(calls)==3: raise RuntimeError('cancelled mid-search')
        with self.assertRaisesRegex(RuntimeError,'mid-search'): solve(parse_problem(domain()), check=check)

    def test_sources_are_hashed_and_never_modified(self):
        before=self.path.read_bytes()
        found=plan_file(self.config,{"path":str(self.path)})
        checked=validate_file(self.config,{"path":str(self.path),"actions":found['plan']})
        self.assertEqual(found['model_sha256'],checked['model_sha256'])
        self.assertEqual(found['source_sha256'],checked['source_sha256'])
        self.assertEqual(self.path.read_bytes(),before)
        self.path.write_text(json.dumps({**domain(),'goal':['at_c']}),encoding='utf-8')
        self.assertNotEqual(plan_file(self.config,{'path':str(self.path)})['model_sha256'], found['model_sha256'])

    def test_plugins_and_explicit_task_route_work_without_model_or_execution(self):
        tools=discover(PROJECT)
        self.assertEqual(tools['formal_plan'].tier,'READ')
        self.assertFalse(tools['formal_plan'].network)
        class NoModel:
            def chat(self,*_): raise AssertionError('Formal route must be deterministic and offline')
        runner=TaskRunner(self.config, tools, Store(self.root),provider=NoModel())
        result=json.loads(runner.run(f'planning: "{self.path}"'))
        self.assertEqual(result['status'],'solved')
        self.assertEqual(runner.successful_tools,1)
        audit=[json.loads(x) for x in (self.root/'logs/audit.jsonl').read_text().splitlines()]
        self.assertEqual(audit[-1]['tool'],'formal_plan')
        self.assertFalse(audit[-1]['result']['executed'])
        selected=select_plugins('formal planning',tools,[])
        self.assertEqual(set(selected),{'formal_plan','formal_validate'})

    def test_policy_still_applies_to_deterministic_route(self):
        tools=discover(PROJECT)
        self.config['permissions']['read']='ask'
        with self.assertRaises(PermissionError): run_plugin(self.config,tools,'formal_plan',{'path':str(self.path)},lambda _:False)
        audit=json.loads((self.root/'logs/audit.jsonl').read_text().splitlines()[-1])
        self.assertFalse(audit['result']['ok'])

    def test_cli_uses_shared_gate_and_failure_exit_status(self):
        from primus.cli import main
        with patch('primus.cli.load_config',return_value=self.config), patch('primus.cli.initialize'), patch('primus.cli.discover',return_value=discover(PROJECT)), patch('builtins.print'):
            self.assertEqual(main(['planning','solve',str(self.path)]),0)
            self.assertEqual(main(['planning','validate',str(self.path),'--actions','to_b','deliver_b']),0)
            self.assertEqual(main(['planning','validate',str(self.path),'--actions','deliver_c']),2)


if __name__=='__main__':
    unittest.main()
