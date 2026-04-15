import json
from datetime import timedelta

import azure.functions as func
import azure.durable_functions as df

app = df.DFApp(http_auth_level=func.AuthLevel.ANONYMOUS)

VALID_CATEGORIES = {"travel", "meals", "supplies", "equipment", "software", "other"}
REQUIRED_FIELDS = [
    "employeeName",
    "employeeEmail",
    "amount",
    "category",
    "description",
    "managerEmail",
]


def build_json_response(payload: dict, status_code: int = 200) -> func.HttpResponse:
    return func.HttpResponse(
        body=json.dumps(payload, indent=2),
        status_code=status_code,
        mimetype="application/json",
    )


# HTTP starter / client function
@app.route(route="start-expense", methods=["POST"])
@app.durable_client_input(client_name="client")
async def start_expense(
    req: func.HttpRequest, client: df.DurableOrchestrationClient
) -> func.HttpResponse:
    try:
        data = req.get_json()
    except ValueError:
        return build_json_response(
            {"error": "Request body must be valid JSON."},
            status_code=400,
        )

    instance_id = await client.start_new("expense_orchestrator", client_input=data)

    return build_json_response(
        {
            "message": "Expense workflow started.",
            "instanceId": instance_id
        },
        status_code=202,
    )


# Orchestrator
@app.orchestration_trigger(context_name="context")
def expense_orchestrator(context: df.DurableOrchestrationContext):
    expense = context.get_input()

    validation_result = yield context.call_activity("validate_expense", expense)

    if not validation_result["isValid"]:
        result = {
            "status": "validation_error",
            "reason": "; ".join(validation_result["errors"]),
            "escalated": False,
        }

        yield context.call_activity(
            "send_employee_notification",
            {"expense": expense, "result": result},
        )
        return result

    amount = float(expense["amount"])

    if amount < 100:
        result = {
            "status": "approved",
            "reason": "Auto-approved because amount is under $100.",
            "escalated": False,
        }

        yield context.call_activity(
            "send_employee_notification",
            {"expense": expense, "result": result},
        )
        return result

    # Manager approval required
    yield context.call_activity("send_manager_request", expense)

    timeout_at = context.current_utc_datetime + timedelta(minutes=2)
    timeout_task = context.create_timer(timeout_at)
    approval_task = context.wait_for_external_event("ManagerDecision")

    winner = yield context.task_any([approval_task, timeout_task])

    if winner == approval_task:
        raw_decision = approval_task.result

        if isinstance(raw_decision, str):
            try:
                decision = json.loads(raw_decision)
            except json.JSONDecodeError:
                decision = raw_decision
        else:
            decision = raw_decision

        decision = str(decision).strip().lower()

        if decision == "approved":
            result = {
                "status": "approved",
                "reason": "Approved by manager.",
                "escalated": False,
            }
        elif decision == "rejected":
            result = {
                "status": "rejected",
                "reason": "Rejected by manager.",
                "escalated": False,
            }
        else:
            result = {
                "status": "validation_error",
                "reason": f"Invalid manager decision received: {decision}",
                "escalated": False,
            }
    else:
        result = {
            "status": "approved",
            "reason": "No manager response before timeout. Auto-approved and flagged as escalated.",
            "escalated": True,
        }

    if not timeout_task.is_completed:
        timeout_task.cancel()

    yield context.call_activity(
        "send_employee_notification",
        {"expense": expense, "result": result},
    )

    return result


# Activity: validation
@app.activity_trigger(input_name="expense")
def validate_expense(expense: dict) -> dict:
    errors = []

    if not isinstance(expense, dict):
        return {
            "isValid": False,
            "errors": ["Expense payload must be a JSON object."]
        }

    for field in REQUIRED_FIELDS:
        value = expense.get(field)
        if value is None:
            errors.append(f"Missing required field: {field}")
        elif isinstance(value, str) and not value.strip():
            errors.append(f"Missing required field: {field}")

    category = str(expense.get("category", "")).lower().strip()
    if category and category not in VALID_CATEGORIES:
        errors.append(
            "Invalid category. Valid categories: travel, meals, supplies, equipment, software, other."
        )

    try:
        amount = float(expense.get("amount", 0))
        if amount < 0:
            errors.append("Amount must be zero or greater.")
    except (TypeError, ValueError):
        errors.append("Amount must be numeric.")

    return {
        "isValid": len(errors) == 0,
        "errors": errors,
    }


# Activity: manager request
@app.activity_trigger(input_name="expense")
def send_manager_request(expense: dict) -> dict:
    print("\n=== MANAGER APPROVAL REQUEST ===")
    print(f"Employee: {expense['employeeName']}")
    print(f"Employee Email: {expense['employeeEmail']}")
    print(f"Amount: {expense['amount']}")
    print(f"Category: {expense['category']}")
    print(f"Description: {expense['description']}")
    print(f"Manager Email: {expense['managerEmail']}")
    print("Use POST /api/manager-decision/{instanceId} with decision approved or rejected")
    print("================================\n")
    return {"sent": True}


# Activity: employee notification
@app.activity_trigger(input_name="payload")
def send_employee_notification(payload: dict) -> dict:
    expense = payload["expense"]
    result = payload["result"]

    print("\n=== EMPLOYEE NOTIFICATION ===")
    print(f"To: {expense.get('employeeEmail', 'unknown')}")
    print(f"Employee: {expense.get('employeeName', 'unknown')}")
    print(f"Final Status: {result['status']}")
    print(f"Reason: {result['reason']}")
    print(f"Escalated: {result['escalated']}")
    print("================================\n")

    return {"notified": True}


# HTTP endpoint: manager approves/rejects
@app.route(route="manager-decision/{instance_id}", methods=["POST"])
@app.durable_client_input(client_name="client")
async def manager_decision(
    req: func.HttpRequest, client: df.DurableOrchestrationClient
) -> func.HttpResponse:
    instance_id = req.route_params.get("instance_id")

    try:
        data = req.get_json()
    except ValueError:
        return build_json_response(
            {"error": "Request body must be valid JSON."},
            status_code=400,
        )

    decision = str(data.get("decision", "")).lower()
    if decision not in {"approved", "rejected"}:
        return build_json_response(
            {"error": "Decision must be 'approved' or 'rejected'."},
            status_code=400,
        )

    await client.raise_event(instance_id, "ManagerDecision", decision)

    return build_json_response(
        {
            "message": f"Manager decision '{decision}' sent to orchestration.",
            "instanceId": instance_id,
        }
    )