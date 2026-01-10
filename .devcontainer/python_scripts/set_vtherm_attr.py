# /config/python_scripts/set_vtherm_attr.py
entity_id = data.get("entity_id")
slope = data.get("slope", 0)

# Récupération de l'état actuel
state = hass.states.get(entity_id)

if state is not None:
    # On crée une copie des attributs existants sans utiliser dict()
    attributes = state.attributes.copy()

    # On injecte ton dictionnaire OBLIGATOIRE
    attributes["specific_states"] = {"temperature_slope": float(slope)}

    # On force la mise à jour de l'entité
    hass.states.set(entity_id, state.state, attributes)
else:
    logger.error("L'entité " + str(entity_id) + " n'a pas été trouvée.")