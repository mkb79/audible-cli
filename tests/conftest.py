import pytest
from helpers import FakeClient


@pytest.fixture
def client():
    return FakeClient()
