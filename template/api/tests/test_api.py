"""The base's contract: health, and every auth endpoint, against a real graph."""


def test_health_reports_the_database(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "db": True}


# --- signup and login --------------------------------------------------------


def test_signup_returns_a_token_that_identifies_the_user(client, user):
    response = client.get("/me", headers=user["headers"])

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "dev@example.test"
    assert body["handle"] == "dev"
    assert "password_hash" not in body


def test_a_taken_email_is_a_409(client, user):
    response = client.post(
        "/signup",
        json={"email": "DEV@example.test", "password": "another password",
              "handle": "other", "name": "Other"},
    )

    assert response.status_code == 409
    assert "email" in response.json()["detail"]


def test_a_taken_handle_is_a_409(client, user):
    response = client.post(
        "/signup",
        json={"email": "other@example.test", "password": "another password",
              "handle": "dev", "name": "Other"},
    )

    assert response.status_code == 409
    assert "handle" in response.json()["detail"]


def test_login_with_the_right_password(client, user):
    response = client.post("/login", json={"email": user["email"], "password": user["password"]})

    assert response.status_code == 200
    assert response.json()["token"]


def test_login_with_the_wrong_password_is_a_401(client, user):
    response = client.post("/login", json={"email": user["email"], "password": "wrong password"})

    assert response.status_code == 401


def test_login_for_an_unknown_email_is_the_same_401(client, user):
    right = client.post("/login", json={"email": user["email"], "password": "wrong password"})
    unknown = client.post("/login", json={"email": "nobody@example.test", "password": "x"})

    assert unknown.status_code == 401
    assert unknown.json() == right.json()


def test_me_without_a_token_is_a_401(client):
    assert client.get("/me").status_code == 401


def test_me_with_a_garbage_token_is_a_401(client):
    assert client.get("/me", headers={"Authorization": "Bearer nope"}).status_code == 401


# --- profile -----------------------------------------------------------------


def test_patch_me_updates_the_profile(client, user):
    response = client.patch("/me", json={"name": "Renamed"}, headers=user["headers"])

    assert response.status_code == 200
    assert response.json()["name"] == "Renamed"


def test_change_password_needs_the_current_one(client, user):
    response = client.post(
        "/me/password",
        json={"current_password": "not it", "new_password": "a brand new one"},
        headers=user["headers"],
    )

    assert response.status_code == 400


def test_change_password_then_login_with_the_new_one(client, user):
    client.post(
        "/me/password",
        json={"current_password": user["password"], "new_password": "a brand new one"},
        headers=user["headers"],
    )

    old = client.post("/login", json={"email": user["email"], "password": user["password"]})
    new = client.post("/login", json={"email": user["email"], "password": "a brand new one"})

    assert old.status_code == 401
    assert new.status_code == 200


# --- password reset ----------------------------------------------------------


def test_forgot_password_mails_a_code_that_resets_the_password(client, user, reset_codes):
    client.post("/forgot-password", json={"email": user["email"]})
    assert len(reset_codes) == 1
    _, code = reset_codes[0]

    response = client.post(
        "/reset-password",
        json={"email": user["email"], "code": code, "new_password": "reset password"},
    )

    assert response.status_code == 200
    login = client.post("/login", json={"email": user["email"], "password": "reset password"})
    assert login.status_code == 200


def test_forgot_password_for_an_unknown_email_answers_the_same(client, user, reset_codes):
    known = client.post("/forgot-password", json={"email": user["email"]})
    unknown = client.post("/forgot-password", json={"email": "nobody@example.test"})

    assert unknown.status_code == known.status_code == 200
    assert unknown.json() == known.json()
    assert len(reset_codes) == 1


def test_a_reset_code_is_spent_by_use(client, user, reset_codes):
    client.post("/forgot-password", json={"email": user["email"]})
    _, code = reset_codes[0]
    client.post("/reset-password", json={"email": user["email"], "code": code,
                                         "new_password": "first new one"})

    replay = client.post("/reset-password", json={"email": user["email"], "code": code,
                                                  "new_password": "second new one"})

    assert replay.status_code == 400


def test_five_wrong_guesses_burn_the_code(client, user, reset_codes):
    client.post("/forgot-password", json={"email": user["email"]})
    _, code = reset_codes[0]
    wrong = "000000" if code != "000000" else "111111"

    for _ in range(5):
        client.post("/reset-password", json={"email": user["email"], "code": wrong,
                                             "new_password": "guessing"})
    response = client.post("/reset-password", json={"email": user["email"], "code": code,
                                                    "new_password": "the right one"})

    assert response.status_code == 400


def test_a_second_request_inside_the_cooldown_sends_nothing(client, user, reset_codes):
    client.post("/forgot-password", json={"email": user["email"]})
    client.post("/forgot-password", json={"email": user["email"]})

    assert len(reset_codes) == 1


# --- deletion ----------------------------------------------------------------


def test_delete_me_ends_the_account(client, user):
    response = client.delete("/me", headers=user["headers"])

    assert response.status_code == 200
    assert client.get("/me", headers=user["headers"]).status_code == 401
    login = client.post("/login", json={"email": user["email"], "password": user["password"]})
    assert login.status_code == 401
