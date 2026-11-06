"""Spending approval happens only when the player says yes."""
import os
os.environ['SOLO_TEST']='1'

import time

from fastapi.testclient import TestClient

from solo.app import create_app


def _pending(app):
    approval = 'ap-1'
    with app.state.costs.db() as db:
        db.execute('INSERT INTO approvals VALUES(?,?,?,?,?,?,?,?,?,?)',
                   (approval, 'fp', 'q', 'RUB', 'turn', 'm', None, 't',
                    time.time() + 600, 'pending'))
    return approval


def _status(app, approval):
    with app.state.costs.db() as db:
        row = db.execute('SELECT status FROM approvals WHERE id=?',
                         (approval,)).fetchone()
    return row['status']


def test_an_unconfirmed_approval_stays_pending(tmp_path):
    app = create_app(tmp_path / 'approve.sqlite3')
    with TestClient(app) as client:
        approval = _pending(app)
        answer = client.post(f'/api/ai/budgets/{approval}/approve',
                             json={'confirm': False})
        assert answer.status_code != 200, (
            'an unconfirmed approval was granted: %s' % answer.status_code)
        assert 'подтверждение' in str(answer.json().get('detail'))
        assert _status(app, approval) == 'pending', (
            'the row moved to %r without a yes' % _status(app, approval))