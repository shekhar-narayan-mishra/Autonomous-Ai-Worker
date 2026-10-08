# Demonstration Script

Welcome to the Autonomous AI Task Worker demo. This script is designed to walk you through the real-time UI, demonstrating the agent's capabilities across normal operation, chaotic infrastructure environments, ambiguous tasks, and independent verification.

## 1. The Plain Task
1. Start the server via `./run_demo.sh` and open the Web UI (`http://localhost:8000`).
2. Ensure the chaos toggles (e.g. `slow_load`, `flaky_500`) are **off**.
3. Select the task **"base_entry"** from the drop-down.
4. Click **Start Run**.
5. **Observe:** The agent seamlessly navigates the Vendor Portal, reads `INV-101`, switches to the Internal ERP, fills out the bill, and submits. The Step Cards will populate smoothly on the left panel.

## 2. The Live Run & Chaos Recovery
1. In the UI, enable the **"Chaos: Flaky 500s"** and **"Chaos: Random Modals"** toggles.
2. Select **"base+chaos"** and click **Start Run**.
3. **Observe:** The mock server will periodically inject `HTTP 500` errors and random annoying popup modals.
4. Watch the agent's Step Cards: The `ReliabilityManager` catches transient 500 errors and automatically retries at the Playwright level (fast recovery). When the popup blocks an element, the agent receives an error, re-plans its approach, clicks the "Close" button on the modal, and continues filling the form.

## 3. The Ambiguity Modal
1. Turn off the Chaos flags.
2. Select the **"ambiguous_duplicate"** task and start the run.
3. **Observe:** The agent attempts to enter the bill but discovers a duplicate or missing data. 
4. The agent uses its `ask_human` tool. The execution halts, and an **Ambiguity Modal** appears in the center of your screen.
5. Provide a clarification (e.g. "Override and create duplicate" or "Ignore the second invoice") and click **Submit**.
6. The agent instantly resumes its sequence with the new context.

## 4. The Verifier Panel
1. Let the agent complete the task.
2. Once the agent emits a `finish` action, the **Verifier Panel** appears on the right side of the screen.
3. **Observe:** An independent rule-based engine evaluates the state of the mock SQLite database. It confirms whether the invoice was accurately recorded (e.g., correct amount, vendor, date, and zero duplicates).
4. If a step was missed (e.g., wrong amount), the verifier fails the run and instantly feeds the error trace back to the agent for one final repair attempt.

## 5. Results & Replay Table
1. After completing several runs, scroll to the bottom to view the **Results Table**.
2. It aggregates metrics: Steps taken, Tokens consumed, specific Provider/Model used, Latency, and Outcome (Success/Failed).
3. **Replay Mode:** Click the **Replay** button next to any completed run. The UI will instantly snap back to that exact session, animating through the stored trace logs at your chosen speed.
