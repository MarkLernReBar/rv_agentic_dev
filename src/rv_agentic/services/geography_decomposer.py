"""Geography decomposition for multi-region discovery strategy.

Splits geographic criteria into non-overlapping regions for sequential
discovery agent calls. This ensures comprehensive coverage and natural
persistence across multiple agent invocations.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


# City/Metro area quadrant definitions
CITY_QUADRANTS = {
    "Denver": [
        {"name": "Downtown Denver & LoDo", "description": "Central Business District, Lower Downtown, Capitol Hill"},
        {"name": "North Denver", "description": "Highlands, RiNo, Five Points, Stapleton"},
        {"name": "South Denver", "description": "Cherry Creek, DTC, Greenwood Village, Englewood"},
        {"name": "West/East Metro", "description": "Lakewood, Westminster, Aurora, Centennial"},
    ],
    "Colorado Springs": [
        {"name": "Central Colorado Springs", "description": "Downtown, Old Colorado City"},
        {"name": "North Colorado Springs", "description": "Academy, Briargate, Rockrimmon"},
        {"name": "South Colorado Springs", "description": "Fort Carson, Fountain, Security-Widefield"},
        {"name": "East Colorado Springs", "description": "Powers, Stetson Hills, Falcon"},
    ],
    "Austin": [
        {"name": "Central Austin", "description": "Downtown, UT area, South Congress"},
        {"name": "North Austin", "description": "Domain, Round Rock, Pflugerville"},
        {"name": "South Austin", "description": "South Lamar, Barton Creek, Sunset Valley"},
        {"name": "East/West Austin", "description": "East Austin, Westlake, Lake Travis"},
    ],
    # Add more cities as needed
}

# State to major cities mapping
STATE_MAJOR_CITIES = {
    "CO": ["Denver", "Colorado Springs", "Aurora", "Fort Collins", "Lakewood"],
    "TX": ["Houston", "Austin", "Dallas", "San Antonio", "Fort Worth"],
    "CA": ["Los Angeles", "San Francisco", "San Diego", "San Jose", "Sacramento"],
    "FL": ["Miami", "Tampa", "Orlando", "Jacksonville", "St. Petersburg"],
    "AZ": ["Phoenix", "Tucson", "Mesa", "Chandler", "Scottsdale"],
    # Add more states as needed
}


def decompose_geography(criteria: Dict[str, Any], num_regions: int = 4) -> List[Dict[str, str]]:
    """
    Decompose geographic criteria into non-overlapping regions.

    Partitioning strategy:
    - If cities specified: Split city into quadrants/neighborhoods
    - If state/geo_markets specified: Split into major cities
    - If multiple states: One region per state

    Args:
        criteria: Run criteria with geo_markets, cities, etc.
        num_regions: Target number of regions (default 4)

    Returns:
        List of region specifications, each with:
        - name: Human-readable region name
        - description: Detailed area description
        - search_focus: Suggested search keywords
    """

    cities = criteria.get("cities", [])
    geo_markets = criteria.get("geo_markets", [])  # State codes
    regions_list = criteria.get("regions", [])  # Multi-state regions

    # Strategy 1: City specified → Split into quadrants
    if cities:
        city = cities[0]  # Use first city if multiple specified

        if city in CITY_QUADRANTS:
            quadrants = CITY_QUADRANTS[city][:num_regions]
            logger.info(f"Decomposed {city} into {len(quadrants)} quadrants")
            return [
                {
                    "name": q["name"],
                    "description": q["description"],
                    "search_focus": f"property management companies in {q['name']}, focusing on {q['description']}",
                }
                for q in quadrants
            ]
        else:
            # City not in our predefined quadrants, use generic split
            logger.warning(f"City {city} not in quadrants map, using generic split")
            return [
                {"name": f"{city} Region {i+1}", "description": f"{city} and surrounding area", "search_focus": f"property management {city}"}
                for i in range(num_regions)
            ]

    # Strategy 2: State specified → Split into major cities
    elif geo_markets:
        state = geo_markets[0]  # Use first state if multiple

        if state in STATE_MAJOR_CITIES:
            cities_list = STATE_MAJOR_CITIES[state][:num_regions]
            logger.info(f"Decomposed {state} into {len(cities_list)} major cities")
            return [
                {
                    "name": f"{city}, {state}",
                    "description": f"{city} metropolitan area",
                    "search_focus": f"property management companies in {city}, {state}",
                }
                for city in cities_list
            ]
        else:
            # State not in our map, use generic approach
            logger.warning(f"State {state} not in cities map, using generic split")
            return [
                {"name": f"{state} Region {i+1}", "description": f"{state} area", "search_focus": f"property management {state}"}
                for i in range(num_regions)
            ]

    # Strategy 3: Multiple states → One region per state
    elif regions_list:
        # For regions like "Southwest", "Mountain West", etc.
        # This would need more sophisticated mapping
        logger.warning(f"Multi-state region {regions_list} specified, using generic split")
        return [
            {"name": f"Region {i+1}", "description": f"Area {i+1}", "search_focus": "property management companies"}
            for i in range(num_regions)
        ]

    # Fallback: No specific geography → Generic regions
    else:
        logger.warning("No specific geography in criteria, using generic regions")
        return [
            {"name": f"Region {i+1}", "description": "Nationwide", "search_focus": "property management companies"}
            for i in range(num_regions)
        ]


def format_region_for_prompt(region: Dict[str, str], criteria: Dict[str, Any]) -> str:
    """Format a region spec into prompt text for the agent.

    Args:
        region: Region specification from decompose_geography
        criteria: Run criteria for additional context

    Returns:
        Formatted prompt snippet describing the region focus
    """

    units_min = criteria.get("units_min", 50)
    units_max = criteria.get("units_max", 50000)

    prompt = (
        f"**YOUR ASSIGNED REGION: {region['name']}**\n\n"
        f"Focus your search EXCLUSIVELY on property management companies in {region['description']}.\n"
        f"Search strategies specific to this region:\n"
        f"- \"{region['search_focus']}\"\n"
        f"- \"{region['name']} apartment management {units_min}+ units\"\n"
        f"- \"multifamily property managers {region['name']}\"\n\n"
        f"DO NOT search outside your assigned region. Other regions are covered separately.\n"
    )

    return prompt
