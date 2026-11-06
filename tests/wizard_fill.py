"""A legal starting build for browser fixtures.

A fixture must not hand-write rule choices: SRD 5.2 asks for skills, expertise, a
background increase, an origin feat and a subclass, and a build that skips them is
correctly refused. This is a thin wrapper over the server's own builder, so the
tests and the advanced catalogue share one implementation instead of restating
rules in test code.
"""
import os

os.environ.setdefault('SOLO_TEST', '1')


def legal_draft(*, klass='rogue', species='human', background='soldier', level=1,
                scores=None, equipment=(), **extra):
    """The wizard draft the same contract the wizard posts to accepts.

    A browser fixture seeds `solo-wizard-draft` with a draft, not with the final
    build payload: the step answers live in `choices`/`equipment_kits`, and a
    payload without them reopens the wizard owing skills, expertise and a feat.
    """
    from solo.nat20_wizard import suggested_draft

    if extra:
        raise TypeError('unexpected: ' + ', '.join(sorted(extra)))
    draft, _final = suggested_draft(
        class_slug=klass, species_slug=species, background_slug=background,
        level=level, ability_scores=scores, equipment=equipment,
    )
    return draft.model_dump(mode='json')


def legal_build(*, klass='rogue', species='human', background='soldier', level=1,
                scores=None, equipment=(), **extra):
    """The build the same contract the wizard posts to accepts."""
    from solo.nat20_wizard import suggested_build

    if extra:
        raise TypeError('unexpected: ' + ', '.join(sorted(extra)))
    return suggested_build(class_slug=klass, species_slug=species,
                           background_slug=background, level=level,
                           ability_scores=scores, equipment=equipment)
