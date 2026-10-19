"""Contract tests for Pi's persistent-orchestrator subprocess delegation."""

import json
import os
import shutil
import tempfile
import unittest

from evals import oracle
from system2_compiler import ir
from system2_compiler.backends.pi import PiBackend


_EXPECTED_ROLE_TOOLS = {
    "code-reviewer": ["Read", "Grep", "Glob", "Bash"],
    "design-architect": ["Read", "Edit", "Write", "Grep", "Glob"],
    "docs-release": ["Read", "Edit", "Write", "Grep", "Glob"],
    "eval-engineer": ["Read", "Edit", "Write", "Grep", "Glob", "Bash"],
    "executor": ["Read", "Edit", "Write", "Grep", "Glob", "Bash"],
    "mcp-toolsmith": ["Read", "Edit", "Write", "Grep", "Glob", "Bash"],
    "postmortem-scribe": ["Read", "Edit", "Write", "Grep", "Glob"],
    "repo-governor": ["Read", "Edit", "Write", "Grep", "Glob", "Bash"],
    "requirements-engineer": ["Read", "Edit", "Write", "Grep", "Glob"],
    "security-sentinel": ["Read", "Edit", "Write", "Grep", "Glob", "Bash"],
    "spec-coordinator": ["Read", "Edit", "Write", "Grep", "Glob"],
    "task-planner": ["Read", "Edit", "Write", "Grep", "Glob"],
    "test-engineer": ["Read", "Edit", "Write", "Grep", "Glob", "Bash"],
}


class PiOrchestrationContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project = tempfile.mkdtemp(prefix="pi-orchestration-contract-")
        result = ir.compose(oracle.PLUGIN_ROOT, [], cls.project)
        if result.graph is None:
            raise AssertionError(result.errors)
        cls.graph = result.graph
        PiBackend().emit(cls.graph, cls.project)
        with open(
            os.path.join(cls.project, ".pi", "extensions", "system2.ts"),
            encoding="utf-8",
        ) as fh:
            cls.extension = fh.read()
        with open(
            os.path.join(cls.project, ".pi", "SYSTEM.md"), encoding="utf-8"
        ) as fh:
            cls.system_prompt = fh.read()
        with open(
            os.path.join(cls.project, "system2.pi.lock.json"), encoding="utf-8"
        ) as fh:
            cls.lock = json.load(fh)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.project, ignore_errors=True)

    def test_ir_preserves_canonical_role_tools(self):
        actual = {role.name: role.tools for role in self.graph.roles}
        self.assertEqual(actual, _EXPECTED_ROLE_TOOLS)

    def test_unknown_canonical_role_tool_fails_compilation(self):
        role = next(role for role in self.graph.roles if role.name == "executor")
        role.tools.append("UnknownHarnessTool")
        try:
            with tempfile.TemporaryDirectory(prefix="pi-unknown-tool-") as project:
                with self.assertRaisesRegex(ValueError, "no explicit tool projection"):
                    PiBackend().emit(self.graph, project)
        finally:
            role.tools.pop()

    def test_fresh_parent_is_orchestrator_not_specialist(self):
        self.assertIn("You are the persistent System2 Orchestrator", self.system_prompt)
        self.assertNotIn("delegate to the 13 roles via `/delegate", self.system_prompt)
        self.assertNotIn("READ_ONLY_DEFAULT_ROLE", self.extension)
        self.assertNotIn("let activeRole", self.extension)
        self.assertIn('mode: "orchestrator"', self.extension)

    def test_model_control_tools_and_human_commands_are_distinct(self):
        for name in (
            "system2_delegate",
            "system2_request_gate_approval",
            "system2_request_gate_skip",
            "system2_status",
            "system2_child_complete",
        ):
            self.assertIn(f'name: "{name}"', self.extension)
        for name in (
            "delegate", "system2-status", "system2-history", "system2-new",
            "system2-resume", "system2-recover",
        ):
            self.assertIn(f'pi.registerCommand("{name}"', self.extension)
        self.assertIn("spawn(", self.extension)
        self.assertIn('"--no-session"', self.extension)
        self.assertIn('"--no-extensions"', self.extension)
        self.assertIn("independent_agent: true", self.extension)
        self.assertIn("SYSTEM2_PI_BIN", self.extension)
        self.assertNotIn('command: "pi"', self.extension)

    def test_parent_can_verify_and_status_without_silent_write_authority(self):
        self.assertIn('"read", "grep", "find", "ls", "bash", "write", "edit"', self.extension)
        self.assertIn('"system2_status", "system2_delegate"', self.extension)
        self.assertIn("async function parentSafetyDecision", self.extension)
        self.assertIn("Parent Bash is available", self.extension)
        self.assertIn("Approve one parent Orchestrator", self.extension)
        self.assertIn("PARENT_OVERRIDE_ENTRY", self.extension)
        self.assertIn("function modelStatusDocument", self.extension)
        self.assertIn("System2 Model Status", self.extension)
        self.assertIn("run verification commands and inspect repository state", self.system_prompt)
        self.assertNotIn("parent tool set excludes bash/write/edit", self.system_prompt)

    def test_lock_describes_proven_isolation_dimensions(self):
        self.assertEqual(self.lock["delegation_mode"], "pi_subprocess")
        self.assertTrue(self.lock["independent_agent"])
        self.assertEqual(
            self.lock["isolation"],
            {
                "process": True,
                "session": True,
                "context": True,
                "filesystem": False,
                "credentials": False,
            },
        )
        self.assertEqual(self.lock["subagent_isolation"], "adapted")

    def test_role_prompt_and_task_lease_are_child_authority(self):
        self.assertIn("ROLE_PROMPT_DIGESTS", self.extension)
        self.assertIn("ROLE_TOOLS", self.extension)
        self.assertIn("roleWriteScope", self.extension)
        self.assertIn("taskWriteScope", self.extension)
        self.assertIn("effective scope is their intersection", self.extension)

    def test_model_supplies_intent_while_extension_derives_authority(self):
        schema = self.extension.split("const DelegationParameters", 1)[1].split(
            "const GateParameters", 1
        )[0]
        for intent_field in (
            "role", "objective", "contextInputs", "expectedOutputs",
            "acceptanceCriteria", "verificationCommands",
        ):
            self.assertIn(intent_field, schema)
        for derived_field in (
            "gateContext", "approvalId", "artifactInputs", "gateEvidence",
            "taskWriteScope",
        ):
            self.assertNotIn(derived_field, schema)
        self.assertIn("Type.Optional", schema)
        self.assertIn("function deriveDelegation", self.extension)
        self.assertIn("expectedOutputs.map(exactOutputScope)", self.extension)
        self.assertIn("snapshotGateEvidence(latest", self.extension)
        self.assertIn("verificationCommands must not contain duplicates", self.extension)

    def test_verification_contracts_are_role_aware_and_missing_events_do_not_trip_circuit(self):
        self.assertIn("delegation verification preflight failed", self.extension)
        self.assertIn("role ${role} has no Bash tool", self.extension)
        self.assertIn("run every verificationCommands entry verbatim", self.extension)
        self.assertIn("const observedVerification = new Set<string>()", self.extension)
        self.assertIn("contracted verification commands were not observed verbatim", self.extension)
        self.assertIn('"VERIFICATION_NOT_RUN"', self.extension)
        self.assertIn('if (code === "VERIFICATION_NOT_RUN") return "contract"', self.extension)
        self.assertIn('status === "not_run"', self.extension)
        self.assertIn("never open the infrastructure circuit", self.system_prompt)
        self.assertIn("conservative additions plus removals", self.system_prompt)
        self.assertIn("corrective decision changes approved requirements", self.system_prompt)
        self.assertIn("treat the stale specification as a Gate 5 blocker", self.system_prompt)

    def test_context_inputs_are_distinct_from_human_approved_gate_evidence(self):
        self.assertIn("function snapshotContextInputs", self.extension)
        self.assertIn("contextInputs: InputSnapshot[]", self.extension)
        self.assertIn("gateEvidence: InputSnapshot[]", self.extension)
        self.assertIn("Context inputs are launch-snapshotted repository data", self.extension)
        self.assertIn("Gate evidence is byte-for-byte human-approved", self.extension)
        self.assertIn("context input changed after dispatch", self.extension)
        self.assertIn("gate evidence digest mismatch", self.extension)
        self.assertNotIn("latest human-approved artifact input", self.extension)

    def test_human_scope_uses_paths_and_globs_not_regexes(self):
        schema = self.extension.split("const GateParameters", 1)[1].split(
            "const CompletionParameters", 1
        )[0]
        self.assertIn("allowedPaths", schema)
        self.assertNotIn("taskWriteScope", schema)
        self.assertIn("normalizeApprovedPathPattern", self.extension)
        self.assertIn("pathMatchesApprovedPattern", self.extension)
        self.assertIn("isSystem2ProtectedPath", self.extension)
        self.assertIn("restricted glob", self.extension)

    def test_gate_four_and_delegation_preflight_role_coverage(self):
        gate_schema = self.extension.split("const GateParameters", 1)[1].split(
            "const CompletionParameters", 1
        )[0]
        self.assertIn("plannedOutputs", gate_schema)
        self.assertIn("function compatibleRolesForOutput", self.extension)
        self.assertIn("function normalizePlannedOutputs", self.extension)
        self.assertIn("Gate 4 approval requires concrete plannedOutputs", self.extension)
        self.assertIn("Planned output role coverage", self.extension)
        self.assertIn("delegation role preflight failed", self.extension)
        self.assertIn("compatible-role coverage matrix", self.system_prompt)

    def test_child_completion_claim_is_small_and_evidence_is_extension_derived(self):
        self.assertIn("type CompletionClaim", self.extension)
        self.assertIn("snapshotProject", self.extension)
        self.assertIn("deriveObservedChanges", self.extension)
        self.assertIn('operationalState:', self.extension)
        self.assertIn('spawnSync("taskkill"', self.extension)
        self.assertIn('Type.Array(ShortText, { maxItems: 64 })', self.extension)
        completion_schema = self.extension.split("const CompletionParameters", 1)[1].split("function isRecord", 1)[0]
        for model_supplied_field in (
            "artifacts", "verification", "filesTouched", "linesAdded", "linesRemoved",
        ):
            self.assertNotIn(model_supplied_field, completion_schema)

    def test_infrastructure_failures_are_separate_and_circuit_broken(self):
        self.assertIn("type FailureDomain", self.extension)
        self.assertIn("countsTowardCorrectiveCycle", self.extension)
        self.assertIn("countsTowardInfrastructureCircuit", self.extension)
        self.assertIn("infrastructureFailureStreak", self.extension)
        self.assertIn("INFRASTRUCTURE_FAILURE_LIMIT = 3", self.extension)
        self.assertIn("System2 infrastructure circuit is open", self.extension)
        self.assertIn('pi.registerCommand("system2-resume"', self.extension)
        self.assertIn("never rolls back delegation changes automatically", self.extension)
        self.assertIn("Only failures explicitly classified application", self.extension)

    def test_gate_decisions_are_visible_skippable_and_workflows_are_repeatable(self):
        self.assertIn('const GATE_AUDIT_MESSAGE = "system2-gate-decision"', self.extension)
        self.assertIn('const WORKFLOW_ARCHIVE_ENTRY = "system2-workflow-archive"', self.extension)
        self.assertIn('decision: "approved" | "rejected" | "skipped"', self.extension)
        self.assertIn("HUMAN APPROVAL REQUIRED", self.extension)
        self.assertIn("selected Approve in the interactive TUI", self.extension)
        self.assertIn("pi.sendMessage({", self.extension)
        self.assertIn('display: true', self.extension)
        self.assertIn("Gate 0 establishes write authority and cannot be skipped", self.extension)
        self.assertIn("Start a new full System2 workflow", self.extension)
        self.assertIn("Start a small maintenance workflow", self.extension)
        self.assertIn('pi.on("input"', self.extension)
        self.assertIn("function startNewWorkflow", self.extension)
        self.assertIn("workflowHistory", self.extension)

    def test_state_is_branch_local_versioned_and_custom_entry_authoritative(self):
        self.assertIn('const WORKFLOW_ENTRY = "system2-workflow-state"', self.extension)
        self.assertIn("type WorkflowState = {\n  schemaVersion: 2;", self.extension)
        self.assertIn("ctx.sessionManager.getBranch()", self.extension)
        self.assertIn("pi.appendEntry(WORKFLOW_ENTRY", self.extension)
        self.assertIn('pi.on("session_tree"', self.extension)
        self.assertIn("interrupted", self.extension)


if __name__ == "__main__":
    unittest.main()
