from core.e2e_workflow_check import average, last_n


def test_average_simple() -> None:
    assert average([1.0, 3.0]) == 2.0


def test_last_n_full() -> None:
    assert last_n([1, 2, 3], 3)[-1] == 3


if __name__ == "__main__":
    test_average_simple()
    test_last_n_full()
    print("ok")
