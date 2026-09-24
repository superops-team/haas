---
name: tauri-react-render-perf
description: Component-level React rendering performance diagnosis for the HaaS Tauri WebView. Use when a live token stream, transcript update, or state change re-renders unrelated UI (sidebar, composer, right rail, inactive routes), when Profiler counts exceed expectations, or when identity/context re-render churn is suspected. Triggers on "re-render", "too many renders", "Profiler", "render churn", "sidebar flicker", "token delta renders everything", "context re-render", or render-performance work in manager/surfaces/gui. Does not cover architecture-level optimization (polling dedup, route splitting) — that belongs to the manager-gui-performance spec.
---

# Tauri React Render Performance

This skill diagnoses and fixes **component-level rendering churn** in the HaaS Manager GUI (`manager/surfaces/gui`). It works with React 18 + local state as it exists today; **React Compiler is not introduced in this wave**, and the patterns below are compiler-agnostic.

## Authority

- Root `AGENTS.md` is authoritative.
- Architecture-level optimization (polling deduplication, route splitting, state boundaries at the request layer) is owned by `specs/manager-gui-performance/` — this skill only covers component-level identity, context slicing, and Profiler verification.
- Generic React rules live in `react-best-practices`; HaaS implementation conventions live in `react-typescript-kit`. This skill does not repeat them — it focuses narrowly on render-churn diagnosis in the Tauri WebView.

## Scope: what this skill covers

Three mechanisms, none of which requires React Compiler:

1. **Identity stability** — derived values, context values, props, and effect deps return stable identities when underlying data is unchanged.
2. **Context boundaries** — narrow subscriptions so identity changes do not re-render every consumer.
3. **Profiler verification** — named Profiler boundaries prove live token deltas do not re-render inactive branches.

## Out of scope (when NOT to use this skill)

- Polling dedup, request single-flight, route-level code splitting, background-surface liveness — these are `manager-gui-performance` P0-1 / architecture concerns.
- Generic React/TypeScript implementation questions — use `react-typescript-kit`.
- Generic Vercel-style React rules of thumb — use `react-best-practices`.
- Build/bundle size, network waterfalls, or Tauri IPC throughput — not render churn.

## Mechanism 1: Identity stability

When data has not changed, the **identity** of derived values, context values, props, and effect dependencies must not change. A new identity forces React to treat the value as changed and re-render consumers.

### Checklists

- **Derived values**: memoize expensive or context-fed derivations with `useMemo` when the result is passed as a prop, context value, or effect dep. Only memoize when the identity actually matters — do not wrap every computation.
- **Reducer no-op updates**: a reducer that returns a new object literal every dispatch (even when state is logically unchanged) re-renders every consumer. Early-return the previous state reference on no-op updates:
  ```ts
  function reducer(state: State, action: Action): State {
    switch (action.type) {
      case 'tick':
        if (state.tick === action.tick) return state; // stable identity
        return { ...state, tick: action.tick };
      default:
        return state;
    }
  }
  ```
- **Hook result destructuring**: destructuring a hook result into a new object/array literal on every render creates a new identity. For hooks that return objects (e.g. local mutation runners, async state wrappers), either return stable references from the hook or destructure only the fields the consumer actually uses, and avoid spreading the whole result into props/context.
- **Object/array literals in props**: `style={{...}}`, `data={[...]}` inline in JSX creates new identity each render. Hoist to module constants or `useMemo` when passed to memoized children or context providers.
- **Effect dependencies**: list only values whose identity genuinely changes when behavior must change. If an effect depends on a derived object, stabilize that derivation with `useMemo` rather than adding suppression comments.

### HaaS local-state note

HaaS uses **component-level React state**, not Redux. Do not reach for `useAppSelector`, Redux selectors, or `react-query` patterns here. Identity stability is achieved by:

- `useMemo` / `useCallback` at the component boundary that owns the state;
- reducers that early-return the previous reference;
- lifting shared state to the nearest common ancestor and passing down **stable** callbacks;
- or, for genuinely cross-surface shared subscriptions, `useSyncExternalStore` with a selector that returns a referentially stable slice (see Mechanism 2).

## Mechanism 2: Context boundaries

`use(Context)` re-renders **every consumer** whenever the context value identity changes — there is no built-in selector. A single broad context that combines live-turn tokens with sidebar preferences means every token delta re-renders the sidebar.

### Checklists

- **Split context by update frequency**. Live-turn token deltas update on every streamed chunk. Sidebar open/closed, right-rail width, composer draft, and route state update rarely. Put them in **separate contexts** so a token delta only notifies live-turn consumers.
- **Provide stable context value identities**. Wrap the provider value in `useMemo` so the context value only changes when its actual contents change:
  ```tsx
  const value = useMemo(() => ({ messages, status }), [messages, status]);
  return <LiveTurnContext.Provider value={value}>{children}</LiveTurnContext.Provider>;
  ```
- **Per-row or per-branch state must not ride a global context**. In a transcript list, each row's expanded/collapsed or streaming state should live in a row-level component (local `useState`) or a narrow subscription, not in a top-level context whose value changes on every row interaction.
- **For cross-surface subscriptions without Redux**: use `useSyncExternalStore` with a snapshot selector that returns a referentially stable slice (e.g. the boolean `isActive` for this branch, not the whole session object). Prefer component-level state for one-owner surfaces; reach for `useSyncExternalStore` only when a surface genuinely needs to subscribe to an external store (the sidecar event stream) without re-rendering on every unrelated event.
- **Do not introduce Redux, zustand, or a new global store** to solve this. HaaS local state is the default; `useSyncExternalStore` is the escape hatch, not the invitation.

## Mechanism 3: Profiler verification

Hypotheses from static analysis are not evidence. Use React Profiler boundaries to prove (or disprove) that a change fixes render churn.

### Acceptance target (from `specs/manager-gui-performance/` P0-2)

> Named React Profiler boundaries around the sidebar, inactive route, composer, and right rail must not invoke `onRender` solely because a token delta arrived.

Concretely: after resetting counters, send at least 30 assistant deltas after `turn_start`. The sidebar, inactive-route, composer, and right-rail boundaries must record **zero** `onRender`; the live transcript boundary updates; the final flush keeps the last delta. This is acceptance criterion FV-GUI-PERF-03 in the performance spec.

### Vitest + Profiler counters

From `manager/surfaces/gui`:

```bash
npm test -- --run <your-render-perf-test>
```

Wrap shell branches with `<Profiler id="sidebar" onRender={capture}>` (and `inactive-route`, `composer`, `right-rail`, `live-transcript`). Drive a running turn over the real GUI `Session` abstraction, advance fake timers, and assert that inactive-branch counters stay at zero while the live-turn counter advances. Mirror the existing `RightRail.preview.test.tsx` style for counter assertions.

### Production build + E2E verification

Render behavior in dev (with React StrictMode double-render and unminified builds) is not representative of the shipped Tauri WebView. Verify against a production bundle and E2E behavior using HEAD-existing entry points:

```bash
cd manager/surfaces/gui
npm run build    # tsc + vite production build; catches type and bundle errors
npm run e2e      # playwright test (HEAD default playwright.config.ts, dev server on :5199)
```

> Note: the default `playwright.config.ts` in HEAD runs against `npm run dev` (port 5199), not a production preview server. `npm run build` confirms the production bundle compiles; `npm run e2e` verifies interaction behavior. A dedicated production-preview Playwright config is not in HEAD and would need to be added before claiming production-bundle E2E evidence.

Use this when a fix needs end-to-end confirmation that real token deltas do not re-render the shell. Dev-only Profiler numbers are a fast loop; production build + E2E is the delivery evidence.

## Workflow

1. **Reproduce churn**: add named Profiler boundaries around the suspected branches, reproduce the live token stream (or the state change), and record `onRender` counts per branch. Do not guess.
2. **Identify the unstable identity**: trace which prop / context value / derived object changes on each token delta. Use React DevTools Profiler "why did this render" or count identity changes in a `useMemo` dep.
3. **Apply the narrowest fix**: stabilize the identity (`useMemo`/`useCallback`, reducer early-return, context split) rather than wrapping every component in `React.memo`.
4. **Re-run the same Profiler counters**: confirm the inactive-branch `onRender` count drops to zero (or matches the expected boundary), and the live transcript still updates correctly.
5. **Run production build + E2E** when the fix touches stream-heavy UI.

## Result format

```text
Churn observed: <symptom, e.g. sidebar re-renders on every token delta>
Profiler baseline: <onRender counts per branch before fix>
Unstable identity: <file:line — the prop/context/derived value changing per delta>
Fix applied: <mechanism 1/2/3 + what stabilized>
Profiler after: <onRender counts per branch after fix>
Production build + E2E: passed|not_run, reason
```

## Source

Adapted from [GitButler `lite-render-perf`](https://github.com/gitbutlerapp/gitbutler/tree/main/.agents/skills/lite-render-perf). Redux / react-query selector examples have been replaced with HaaS local-state and `useSyncExternalStore` patterns; React Compiler memoization analysis is intentionally dropped (not introduced in this wave). No external code retained.
