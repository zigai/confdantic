import pytest


@pytest.fixture
def sample_data():
    return {
        "name": "John Doe",
        "age": 30,
        "hobbies": ["reading", "cycling"],
        "address": "123 Main St",
    }
