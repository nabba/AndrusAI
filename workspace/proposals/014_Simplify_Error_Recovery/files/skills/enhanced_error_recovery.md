Skill to handle and recover from ValueError and BadRequestError related to LLM responses. Focuses on:
1. Detailed error logging with root cause analysis
2. Precise error message parsing for Gemini API errors
3. Fallback mechanisms for empty/invalid responses
4. Clear error reporting to users

Removed redundant retry logic since it's handled at the wrapper level.