# ADR 001: Plain and Simple Code (KISS, SOTA, No Hexagonal, No DDD, No TDD)

## Status
Approved

## Context
For the `IRPF-CORR` project, we require a highly maintainable, robust, and performant codebase. Historically, enterprise design patterns such as Hexagonal Architecture (Ports and Adapters) or Clean Architecture have been proposed to decouple domain logic from infrastructure details. 

However, in this specific project context, Hexagonal Architecture introduces excessive boilerplate, deep nested directory hierarchies, extensive abstraction overhead (ports, adapters, interfaces, abstract base classes for every single I/O dependency), and increased cognitive load. This level of abstraction is counter-productive to the principles of simplicity, execution velocity, and direct maintainability.

## Decision
We will strictly enforce the following architectural rules:
1. **No Hexagonal, No DDD, No TDD**: We will **NEVER** use Hexagonal Architecture (Ports & Adapters), tactical Domain-Driven Design (DDD) overhead, or Test-Driven Development (TDD) in this project. No automated testing frameworks (like pytest) are allowed in any stage of this project.
2. **Elegant Modular Monolith**: The codebase is designed as a clean, elegant modular monolith. Logic is divided into cohesive, straightforward modules rather than complex distributed domains or layered architectures, making implementation and debugging fast and direct.
3. **KISS Principle (Keep It Plain and Simple)**: Code should be direct, clean, and highly readable. Avoid over-engineering, unnecessary abstraction layers, and pre-emptive generalizations. Prefer straightforward modules, functions, and classes that solve the immediate requirements.
4. **State of the Art (SOTA) Tooling & Standards**: We will maintain high engineering quality by utilizing modern, high-performance tooling:
   - **Environment & Dependency Management**: Fast package resolution using `uv` and standard modern `pyproject.toml`.
   - **Configuration Validation**: Strongly-typed configuration centralized in `config.py` using `pydantic-settings` to parse `.env` files, ensuring fail-fast principles on startup.
   - **Formatting & Linting**: Super-fast formatting and linting via `ruff`.
5. **No Background Execution**: The AI agent must **never** run scripts in the background. All execution of scraper or classification scripts by the agent must be synchronous/foreground, and generally running scripts is reserved for interactive IDE debugging by the user until further notice.
6. **Language Standard**: All source code, comments, docstrings, variable names, function names, class names, logs, error messages, and documentation must be written strictly in English. While communication with the user can be in Portuguese, the codebase itself must contain no Portuguese.

## Consequences
- **Positive**:
  - Significant reduction in boilerplate and cognitive overhead.
  - High developer velocity and direct, readable flows of execution.
  - Zero performance cost from excessive abstraction layers.
  - Frictionless onboarding.
- **Negative/Neutral**:
  - The domain logic and infrastructure/I/O code will be more closely aligned, but we will preserve clean modular design principles to keep them decoupled pragmatically without the boilerplate of formal Ports and Adapters.
