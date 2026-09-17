from .ai import CoachAI, AIError


def evaluate_attempt(repo, principal, user_id, attempt_id, settings, ai=None):
    attempt = repo.get_attempt(principal, user_id, attempt_id)
    if attempt["status"] == "completed":
        return attempt
    token = repo.claim_evaluation(principal, user_id, attempt_id)
    if not token:
        raise AIError("This evaluation is already running. Refresh Progress in a moment; an interrupted request can be retried after three minutes.")
    try:
        result = (ai or CoachAI(settings)).evaluate(attempt)
    except AIError as exc:
        repo.finish_evaluation(principal, user_id, attempt_id, token, error=str(exc))
        raise
    except Exception:
        repo.finish_evaluation(principal, user_id, attempt_id, token,
                               error="Evaluation was interrupted. Your answer is saved; please retry.")
        raise AIError("Evaluation was interrupted. Your answer is saved; please retry.") from None
    repo.finish_evaluation(principal, user_id, attempt_id, token, evaluation=result)
    return repo.get_attempt(principal, user_id, attempt_id)
