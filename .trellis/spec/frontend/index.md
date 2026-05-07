# Frontend Development Guidelines

> Best practices for frontend development in this project.

---

## Overview

The UI is a **single-page Vue 3** app (script setup + Composition API) shipped with Vite. It posts natural language questions to `POST /api/query` and renders SQL, explanations, status, and optional tabular results.

---

## Guidelines Index

| Guide | Description | Status |
|-------|-------------|--------|
| [Directory Structure](./directory-structure.md) | Module organization and file layout | Filled |
| [Component Guidelines](./component-guidelines.md) | Components, composition, UI patterns | Filled |
| [Hook Guidelines](./hook-guidelines.md) | Composables and data flow | Filled |
| [State Management](./state-management.md) | Local state and API state | Filled |
| [Quality Guidelines](./quality-guidelines.md) | Code standards, tooling | Filled |
| [Type Safety](./type-safety.md) | Types and API shapes | Filled |

---

## How to Fill These Guidelines

For each guideline file:

1. Document your project's **actual conventions** (not ideals)
2. Include **code examples** from your codebase
3. List **forbidden patterns** and why
4. Add **common mistakes** your team has made

The goal is to help AI assistants and new team members understand how YOUR project works.

---

**Language**: All documentation should be written in **English**.
