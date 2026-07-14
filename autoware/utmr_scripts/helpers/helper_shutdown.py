def is_expected_shutdown_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    expected_fragments = (
        "context is invalid",
        "rcl_shutdown",
        "rcl_init() was not called",
        "failed to initialize wait set",
    )
    return any(fragment in text for fragment in expected_fragments)
