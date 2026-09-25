<?php
declare(strict_types=1);

namespace vielhuber\backuphelper;

final readonly class Job
{
    /**
     * @param list<string> $exclude
     * @param list<Source> $sources
     */
    public function __construct(
        public string $name,
        public string $target,
        public int $keep,
        public int $interval,
        public array $exclude,
        public array $sources
    ) {}
}
