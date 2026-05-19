# ADR 002: Centralized Constants and Global Variables

## Status
Approved

## Context
Hardcoding configurations, API endpoints, URLs, default timeouts, or file paths directly inside nested loops or functions makes the codebase difficult to maintain, test, and adapt. Finding scattered variables slows down development and increases the risk of inconsistencies (e.g., mismatched paths or API endpoints).

## Decision
We will strictly enforce the following rules for constants, imports, and global structure:
1. **No Embedded Hardcoded Values**: All constants (URLs, API endpoints, base payload structures, default timeouts, file paths) must be defined as global variables at the top of the file (or immediately following imports).
2. **Imports First**: All module imports must be placed at the very top of the file, organized according to standard convention (standard library, third-party libraries, local modules).
3. **Upper Case for Constants**: Global constants should be named using `UPPER_CASE_WITH_UNDERSCORES` to clearly differentiate them from local variables.
4. **Standard File Structure**:
   - Imports
   - Constants / Global Configurations
   - Helper Functions / Classes
   - Core Logic / Entrypoints (e.g., `main()`)

## Consequences
- **Positive**:
  - Configuration changes can be made in a single place without searching through code.
  - Better readability and cleaner functions that focus strictly on logic.
  - Easier transition to environment variable overrides (e.g., using `pydantic-settings` or `.env`) if needed in the future.
- **Negative/Neutral**:
  - Slightly more variables declared at the module level.
