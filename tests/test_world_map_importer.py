from src.antibot_cv.automation.world_map_importer import merge_world_map, parse_world_map


def test_world_map_parser_preserves_unbound_npcs_and_merges_graph() -> None:
    dataset = parse_world_map(
        compass_xml='''<locations>
          <loc id="1" verges="2,3|2,4"/><loc id="2" verges="1,3"/>
        </locations>''',
        area_xml_by_source={"area.xml": '''<areas><area id="9">
          <location id="1"><title>One</title><object id="chel_gray">
            <title>NPC</title><item>Alice</item><item>Bob</item>
          </object></location>
          <location id="2"><title>Two</title></location>
          <location id="3"><title>Three</title></location>
          <location id="4"><title>Four</title></location>
        </area></areas>'''},
    )
    data = {
        "schemaVersion": 1, "locations": {}, "edges": {},
        "npcs": {"1:7": {"locationId": "1", "areaObjectId": "7", "name": "Alice"}},
        "instances": {},
    }

    merge_world_map(data, dataset)

    assert data["locations"]["2"]["name"] == "Two"
    assert data["edges"]["1"]["2"] == {"to": "2", "name": "Two"}
    assert data["edges"]["1"]["4"] == {"to": "4", "name": "Four"}
    assert data["mapNpcs"]["1"]["names"] == ["Alice", "Bob"]
    assert data["npcs"]["1:7"]["areaObjectId"] == "7"
