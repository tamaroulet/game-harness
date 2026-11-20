#!/bin/sh
# agy PreToolUse hook: block subagents (invoke_subagent). A subagent can pick another model and its steps do not
# appear in the parent's stream-json, so the model pin and the post-run audit cannot cover it. Reads the hook input and denies.
cat > /dev/null
echo '{"decision": "deny", "reason": "Subagents are not available in this environment."}'
