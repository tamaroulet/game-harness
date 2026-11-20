#!/bin/sh
# agy PreToolUse hook: block web tools (search_web, read_url_content, browser). Reads the hook input and denies.
cat > /dev/null
echo '{"decision": "deny", "reason": "Web access is not available in this environment."}'
