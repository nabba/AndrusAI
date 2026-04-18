List<Person> result = people.stream()
    .filter(p -> p.billions >= 100)
    .sorted(Comparator.comparing(p -> p.name))
    .collect(Collectors.toList());