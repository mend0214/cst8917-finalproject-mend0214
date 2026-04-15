import json
import azure.functions as func

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

VALID_CATEGORIES = {"travel", "meals", "supplies", "equipment", "software", "other"}
REQUIRED_FIELDS = [
    "employeeName",
    "employeeEmail",
    "amount",
    "category",
    "description",
    "managerEmail",
]

@app.route(route="validate-expense", methods=["POST"])
def validate_expense(req: func.HttpRequest) -> func.HttpResponse:
    try:
        try:
            expense = req.get_json()
        except ValueError:
            raw_body = req.get_body()
            expense = json.loads(raw_body.decode("utf-8"))
    except Exception:
        return func.HttpResponse(
            json.dumps({
                "isValid": False,
                "errors": ["Request body must be valid JSON."]
            }),
            status_code=400,
            mimetype="application/json"
        )

    errors = []

    if not isinstance(expense, dict):
        errors.append("Expense payload must be a JSON object.")
    else:
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

    response = {
        "isValid": len(errors) == 0,
        "errors": errors
    }

    return func.HttpResponse(
        json.dumps(response),
        status_code=200,
        mimetype="application/json"
    )