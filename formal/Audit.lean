import Provenance

-- These reports must contain no sorryAx. The checker uses no custom axioms or
-- native_decide; check_iff is proved by the kernel from ordinary core lemmas.
#print axioms Provenance.check_iff
#print axioms Provenance.accepted_has_passing_tests
#print axioms Provenance.accepted_tests_exact_artifact
#print axioms Provenance.accepted_build_and_test_same_source
#print axioms Provenance.changed_artifact_rejected
#print axioms Provenance.accepted_has_complete_calls
