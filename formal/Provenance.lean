import Lean

namespace Provenance

structure Call where
  id : String
  preCount : Nat
  postCount : Nat
  status : String
  deriving Lean.FromJson, Lean.ToJson

structure Session where
  id : String
  head : String
  eventCount : Nat
  started : Bool
  stopped : Bool
  interrupted : Bool
  calls : List Call
  deriving Lean.FromJson, Lean.ToJson

structure Build where
  commandDigest : String
  exitCode : Int
  sourceBefore : String
  sourceAfter : String
  artifactDigest : String
  deriving Lean.FromJson, Lean.ToJson

structure Test where
  commandDigest : String
  exitCode : Int
  sourceBefore : String
  sourceAfter : String
  artifactBefore : String
  artifactAfter : String
  deriving Lean.FromJson, Lean.ToJson

structure Certificate where
  schemaVersion : Nat
  runId : String
  baseRevision : String
  agentCommandDigest : String
  agentExitCode : Int
  sourceDigest : String
  artifactDigest : String
  build : Build
  test : Test
  sessions : List Session
  deriving Lean.FromJson, Lean.ToJson

/-- Supplied independently by the consuming CI job, never read from the certificate. -/
structure Policy where
  runId : String
  baseRevision : String
  agentCommandDigest : String
  buildCommandDigest : String
  testCommandDigest : String
  artifactDigest : String
  deriving Lean.FromJson, Lean.ToJson

def CallComplete (c : Call) : Prop :=
  c.id ≠ "" ∧ c.preCount = 1 ∧ c.postCount = 1 ∧
    (c.status = "succeeded" ∨ c.status = "failed" ∨ c.status = "returned")

instance (c : Call) : Decidable (CallComplete c) := inferInstanceAs (Decidable (_ ∧ _))

def SessionComplete (s : Session) : Prop :=
  s.id ≠ "" ∧ s.head ≠ "" ∧ s.eventCount > 0 ∧
  s.started = true ∧ s.stopped = true ∧ s.interrupted = false ∧
  ∀ c ∈ s.calls, CallComplete c

instance (s : Session) : Decidable (SessionComplete s) :=
  inferInstanceAs (Decidable (_ ∧ _))

/-- The release policy describes recorded evidence, not authenticity of observations. -/
def Valid (p : Policy) (c : Certificate) : Prop :=
  c.schemaVersion = 1 ∧
  c.runId = p.runId ∧ c.baseRevision = p.baseRevision ∧
  c.agentCommandDigest = p.agentCommandDigest ∧
  c.build.commandDigest = p.buildCommandDigest ∧
  c.test.commandDigest = p.testCommandDigest ∧
  c.sourceDigest ≠ "" ∧ c.artifactDigest ≠ "" ∧
  c.artifactDigest = p.artifactDigest ∧
  c.agentExitCode = 0 ∧ c.build.exitCode = 0 ∧ c.test.exitCode = 0 ∧
  c.build.sourceBefore = c.sourceDigest ∧ c.build.sourceAfter = c.sourceDigest ∧
  c.test.sourceBefore = c.sourceDigest ∧ c.test.sourceAfter = c.sourceDigest ∧
  c.build.artifactDigest = c.artifactDigest ∧
  c.test.artifactBefore = c.artifactDigest ∧ c.test.artifactAfter = c.artifactDigest ∧
  c.sessions ≠ [] ∧
  (∃ s ∈ c.sessions, s.calls ≠ []) ∧
  ∀ s ∈ c.sessions, SessionComplete s

instance (p : Policy) (c : Certificate) : Decidable (Valid p c) :=
  inferInstanceAs (Decidable (_ ∧ _))

def checkCall (c : Call) : Bool :=
  c.id != "" && c.preCount == 1 && c.postCount == 1 &&
    (c.status == "succeeded" || c.status == "failed" || c.status == "returned")

def checkSession (s : Session) : Bool :=
  s.id != "" && s.head != "" && s.eventCount > 0 &&
  s.started && s.stopped && !s.interrupted && s.calls.all checkCall

def check (p : Policy) (c : Certificate) : Bool :=
  c.schemaVersion == 1 &&
  c.runId == p.runId && c.baseRevision == p.baseRevision &&
  c.agentCommandDigest == p.agentCommandDigest &&
  c.build.commandDigest == p.buildCommandDigest &&
  c.test.commandDigest == p.testCommandDigest &&
  c.sourceDigest != "" && c.artifactDigest != "" &&
  c.artifactDigest == p.artifactDigest &&
  c.agentExitCode == 0 && c.build.exitCode == 0 && c.test.exitCode == 0 &&
  c.build.sourceBefore == c.sourceDigest && c.build.sourceAfter == c.sourceDigest &&
  c.test.sourceBefore == c.sourceDigest && c.test.sourceAfter == c.sourceDigest &&
  c.build.artifactDigest == c.artifactDigest &&
  c.test.artifactBefore == c.artifactDigest && c.test.artifactAfter == c.artifactDigest &&
  !c.sessions.isEmpty && c.sessions.any (fun s => !s.calls.isEmpty) &&
  c.sessions.all checkSession

theorem checkCall_iff (c : Call) : checkCall c = true ↔ CallComplete c := by
  simp [checkCall, CallComplete, and_assoc, or_assoc]

theorem checkSession_iff (s : Session) : checkSession s = true ↔ SessionComplete s := by
  simp [checkSession, SessionComplete, checkCall_iff, and_assoc]

/-- The executable checker accepts exactly the formal release policy. -/
theorem check_iff (p : Policy) (c : Certificate) : check p c = true ↔ Valid p c := by
  simp [check, Valid, checkSession_iff, and_assoc]

theorem accepted_has_passing_tests (p : Policy) (c : Certificate)
    (h : check p c = true) : c.test.exitCode = 0 := by
  have hv := (check_iff p c).mp h
  simp only [Valid] at hv
  rcases hv with ⟨_, _, _, _, _, _, _, _, _, _, _, ht, _⟩
  exact ht

theorem accepted_tests_exact_artifact (p : Policy) (c : Certificate)
    (h : check p c = true) :
    c.test.artifactBefore = p.artifactDigest ∧ c.test.artifactAfter = p.artifactDigest := by
  have hv := (check_iff p c).mp h
  simp only [Valid] at hv
  rcases hv with ⟨_, _, _, _, _, _, _, _, ha, _, _, _, _, _, _, _, _, hb, hc, _⟩
  exact ⟨hb.trans ha, hc.trans ha⟩

theorem accepted_build_and_test_same_source (p : Policy) (c : Certificate)
    (h : check p c = true) :
    c.build.sourceBefore = c.test.sourceBefore ∧
    c.build.sourceAfter = c.test.sourceAfter := by
  have hv := (check_iff p c).mp h
  simp only [Valid] at hv
  rcases hv with ⟨_, _, _, _, _, _, _, _, _, _, _, _, hb, ha, ht, hu, _⟩
  exact ⟨hb.trans ht.symm, ha.trans hu.symm⟩

theorem changed_artifact_rejected (p : Policy) (c : Certificate)
    (changed : c.test.artifactAfter ≠ p.artifactDigest) : check p c = false := by
  cases h : check p c with
  | false => rfl
  | true => exact False.elim (changed (accepted_tests_exact_artifact p c h).2)

theorem accepted_has_complete_calls (p : Policy) (c : Certificate)
    (h : check p c = true) (s : Session) (hs : s ∈ c.sessions)
    (call : Call) (hc : call ∈ s.calls) : CallComplete call := by
  have hv := (check_iff p c).mp h
  simp only [Valid] at hv
  rcases hv with ⟨_, _, _, _, _, _, _, _, _, _, _, _, _, _, _, _, _, _, _, _, _, sessions⟩
  have complete := sessions s hs
  simp only [SessionComplete] at complete
  rcases complete with ⟨_, _, _, _, _, _, calls⟩
  exact calls call hc

end Provenance
