from plugins.sample_module import SampleModule


def test_add_returns_sum() -> None:
    plugin = SampleModule(name="demo", data="value")
    assert plugin.add(2, 3) == 5


def test_process_formats_name_and_data() -> None:
    plugin = SampleModule(name="demo", data="value")
    assert plugin.process() == "demo: value"
