from scripts.evaluate_conversation_memory import run_evaluation


def test_deterministic_conversation_memory_evaluation_scenarios():
    result = run_evaluation()
    assert result['scenario_count'] == 13
    assert result['passed_scenarios'] == 13
    assert result['scenario_accuracy'] == 1.0
    assert all(item['accuracy'] == 1.0 for item in result['metrics'].values())
