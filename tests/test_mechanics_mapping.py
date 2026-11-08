"""The edition boundary and the mechanics mapping layer.

A 2014 book must run on 2024 mechanics without being blocked by the difference, and
every replacement must be recorded with its source wording and reason. These tests
pin that contract: the book's edition and the mechanics edition stay two facts, a
lookalike name never becomes a canonical entity, and an unsupported element is
reported instead of being executed or used to refuse the whole adventure.
"""
import pytest
from solo.adventure_contracts import AdventureDefinition
from solo.mechanics_mapping import (MechanicsMapping, Substitution, adapt, build,
                                    resolve_check, resolve_monster)


def test_book_edition_and_mechanics_edition_are_separate_facts():
    definition = AdventureDefinition(id='m', title='t', ruleset='dnd2024',
        ruleset_version=1, source_ruleset='dnd2014', source_ruleset_version=1)
    # The mechanics preset is what executes; the source edition is what the text is.
    assert definition.ruleset == 'dnd2024'
    assert definition.source_ruleset == 'dnd2014'


def test_missing_source_edition_stays_unknown_instead_of_guessed():
    definition = AdventureDefinition(id='m', title='t', ruleset='dnd2024', ruleset_version=1)
    assert definition.source_ruleset == ''


def test_verified_alias_resolves_to_a_real_canonical_monster():
    row = resolve_monster('кобольд', page=10)
    assert row.target == 'kobold-warrior' and row.basis == 'alias'
    assert row.source == 'кобольд'


def test_transliteration_difference_is_an_alias_not_a_fabrication():
    # Cyrillic and Latin spellings of the same stat block are one reviewed alias.
    assert resolve_monster('кобольд', page=10).target == resolve_monster('kobold warrior', page=17).target


def test_name_similarity_alone_never_becomes_a_target():
    # "сторожевой дрейк" is a real book monster with no canonical entry. A lookalike
    # must not be substituted just because the words resemble something.
    row = resolve_monster('сторожевой дрейк', page=19)
    assert row.target == '' and row.basis == 'none'
    assert 'адаптации' in row.reason


def test_check_uses_the_skill_inside_the_brackets():
    row = resolve_check('Ловкость (Ловкость Рук)', page=21)
    assert row.target == 'sleight_of_hand' and row.basis == 'alias'


def test_inflected_check_phrase_still_resolves():
    # The book writes the genitive "Обмана"; it is the same Deception check.
    assert resolve_check('Проверка Харизмы (Обмана)', page=20).target == 'deception'


def test_unknown_check_is_reported_rather_than_invented():
    # No such skill exists in the corpus, so nothing is invented for it.
    row = resolve_check('Проверка Удачи', page=20)
    assert row.target == '' and row.basis == 'none'


def test_unresolved_element_moves_to_adaptation_and_is_never_executed():
    mapping = build(source_ruleset='dnd2014', source_version=1,
                    mechanics_ruleset='dnd2024', mechanics_version=1,
                    substitutions=[resolve_monster('кобольд', page=10),
                                   resolve_monster('сторожевой дрейк', page=19)])
    # Only the element with a real counterpart stays a substitution.
    assert [s.source for s in mapping.substitutions] == ['кобольд']
    sources = [a.source for a in mapping.adaptations]
    assert 'сторожевой дрейк' in sources


def test_one_unsupported_ability_does_not_make_the_mapping_unusable():
    mapping = build(source_ruleset='dnd2014', source_version=1,
                    mechanics_ruleset='dnd2024', mechanics_version=1,
                    substitutions=[resolve_monster('культист', page=17)],
                    adaptations=[adapt('rule', 'опыт за монстров', 'Нет таблицы опыта.', page=18)])
    assert len(mapping.resolved) == 1
    assert len(mapping.requires_adaptation) == 1


def test_substitution_without_basis_is_rejected():
    with pytest.raises(ValueError):
        Substitution(kind='monster', source='x', target='kobold-warrior', basis='none', reason='r')


def test_balance_is_never_promised_as_exact():
    mapping = build(source_ruleset='dnd2014', source_version=1,
                    mechanics_ruleset='dnd2024', mechanics_version=1)
    assert 'не обещается' in mapping.balance_note


def test_public_projection_states_replacements_and_adaptations():
    mapping = build(source_ruleset='dnd2014', source_version=1,
                    mechanics_ruleset='dnd2024', mechanics_version=1,
                    substitutions=[resolve_monster('кобольд', page=10)],
                    adaptations=[adapt('rule', 'опыт', 'Не подтверждён.', page=18)])
    public = mapping.public()
    assert public['source_ruleset'] == 'dnd2014' and public['mechanics_ruleset'] == 'dnd2024'
    row = public['replaced'][0]
    assert row['source'] == 'кобольд' and row['target'] == 'kobold-warrior' and row['reason']
    assert public['needs_adaptation'][0]['needs']


def test_repeated_source_element_is_refused():
    with pytest.raises(ValueError):
        MechanicsMapping(source_ruleset='dnd2014', source_version=1,
            mechanics_ruleset='dnd2024', mechanics_version=1,
            substitutions=[resolve_monster('кобольд', page=10), resolve_monster('Кобольд', page=11)])
