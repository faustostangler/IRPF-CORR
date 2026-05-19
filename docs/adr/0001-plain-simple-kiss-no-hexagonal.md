# ADR 001: Plain and Simple Code (KISS, SOTA, No Hexagonal Architecture)

## Status
Approved

## Context
For the `IRPF-CORR` project, we require a highly maintainable, robust, and performant codebase. Historically, enterprise design patterns such as Hexagonal Architecture (Ports and Adapters) or Clean Architecture have been proposed to decouple domain logic from infrastructure details. 

However, in this specific project context, Hexagonal Architecture introduces excessive boilerplate, deep nested directory hierarchies, extensive abstraction overhead (ports, adapters, interfaces, abstract base classes for every single I/O dependency), and increased cognitive load. This level of abstraction is counter-productive to the principles of simplicity, execution velocity, and direct maintainability.

## Decision
We will strictly enforce the following architectural rules:
1. **No Hexagonal Architecture**: We will **NEVER** use Hexagonal Architecture, Ports & Adapters, or similar highly-layered enterprise abstraction frameworks in this project.
2. **KISS Principle (Keep It Plain and Simple)**: Code should be direct, clean, and highly readable. Avoid over-engineering, unnecessary abstraction layers, and pre-emptive generalizations. Prefer straightforward modules, functions, and classes that solve the immediate requirements.
3. **State of the Art (SOTA) Tooling & Standards**: We will maintain high engineering quality by utilizing modern, high-performance tooling:
   - **Environment & Dependency Management**: Fast package resolution using `uv` and standard modern `pyproject.toml`.
   - **Configuration Validation**: Strongly-typed configuration centralized in `config.py` using `pydantic-settings` to parse `.env` files, ensuring fail-fast principles on startup.
   - **Formatting & Linting**: Super-fast formatting and linting via `ruff`.
   - **Testing**: Robust, straightforward test suites using `pytest` without overly complex mocking structures.
4. **Pragmatic Modular Structure**: Instead of layers of ports and adapters, code will be organized in functional cohesive modules representing the domains or components directly, keeping the codebase easy to navigate and inspect.

## Consequences
- **Positive**:
  - Significant reduction in boilerplate and cognitive overhead.
  - High developer velocity and direct, readable flows of execution.
  - Zero performance cost from excessive abstraction layers.
  - Frictionless onboarding and testing.
- **Negative/Neutral**:
  - The domain logic and infrastructure/I/O code will be more closely aligned, but we will preserve clean modular design principles to keep them decoupled pragmatically without the boilerplate of formal Ports and Adapters.
