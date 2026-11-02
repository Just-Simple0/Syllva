"""Safe MCP error messages without provider payloads or user content."""

def safe_error_message(code: str) -> str:
    return {
        'RESOLUTION_EXPIRED': 'Repeat the parent entity-resolution call.',
        'CONTEXT_EXPIRED': 'Repeat the parent context call and retry the chunk once.',
        'LOCATOR_STALE': 'Repeat the parent context call; do not reuse the old locator.',
        'LOCATOR_NOT_ALLOWED': 'The locator is not authorized by this context.',
        'INVALID_SUBMISSION_REF': 'The stored submission commit or tag is unavailable.',
        'ENTITY_NOT_FOUND': 'No matching academic entity was found.',
        'SOURCE_UNAVAILABLE': 'Source evidence is unavailable.',
        'POLICY_DENIED': 'Retrieval policy denied this request.',
    }.get(code, 'The operation could not provide current source evidence.')
