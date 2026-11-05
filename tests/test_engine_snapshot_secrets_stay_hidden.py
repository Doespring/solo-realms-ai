"""A secret inside the engine snapshot never reaches the Master prompt."""
import os
os.environ['SOLO_TEST']='1'

from solo.game import SceneCompiler


def test_a_secret_inside_the_engine_snapshot_is_dropped():

    class FakeSnapshot:
        def model_dump(self, mode='json'):
            return dict(turn=1, notes=
                        dict(scope='secret', text='The duke poisoned the well.'),
                        public='The well is dry.',
                        nested=[dict(scope='gm', text='Trap on the bridge.')])

    snapshot = FakeSnapshot().model_dump()
    shown = SceneCompiler.visible(snapshot)
    text = repr(shown)
    assert 'poisoned the well' not in text
    assert 'Trap on the bridge' not in text
    assert 'The well is dry.' in text